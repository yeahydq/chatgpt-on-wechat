import asyncio
import time
import requests
import os
import base64

import web
from wechatpy import parse_message
from wechatpy.replies import ImageReply, VoiceReply, create_reply
import textwrap
from bridge.context import *
from bridge.reply import *
from channel.wechatmp.common import *
from channel.wechatmp.wechatmp_channel import WechatMPChannel
from channel.wechatmp.wechatmp_message import WeChatMPMessage
from common.log import logger
from common.utils import split_string_by_utf8_length
from config import conf, subscribe_msg


def call_remote_image_api(image_path, question_content="", subject="数学", grade="初中"):
    """
    调用远端API处理图片（类似 /api/analyze-answer 接口）
    :param image_path: 本地图片路径
    :param question_content: 问题内容（可选）
    :param subject: 科目（默认：数学）
    :param grade: 年级（默认：初中）
    :return: API返回的结果文本
    """
    try:
        # 从配置文件中获取API相关配置
        api_url = conf().get("image_api_url")

        if not api_url:
            logger.warning("[wechatmp] image_api_url not configured")
            return "图片处理API未配置，请在config.json中设置image_api_url"

        logger.info(f"[wechatmp] Calling remote image API: {api_url} with image: {image_path}")

        # 读取图片文件并转换为base64
        with open(image_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')

        # 构建请求数据
        payload = {
            "image_data": image_data,
            "question_content": question_content,
            "subject": subject,
            "grade": grade
        }

        # 设置请求头
        headers = {
            'Content-Type': 'application/json',
        }

        # 发送POST请求到远端API
        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=60  # 图片分析可能需要较长时间
        )

        if response.status_code == 200:
            # 解析API返回结果
            result = response.json()
            logger.info(f"[wechatmp] Image API response: {result}")

            # 根据实际API返回格式提取结果
            # 假设返回格式为 {"result": "分析结果", "success": true}
            if isinstance(result, dict):
                if result.get('success') or result.get('result'):
                    return result.get('result', result.get('answer', str(result)))
                else:
                    error_msg = result.get('error', result.get('message', '未知错误'))
                    return f"图片分析失败: {error_msg}"
            else:
                return str(result)
        else:
            logger.error(f"[wechatmp] Image API error: {response.status_code}, {response.text}")

            # 检查是否是 IP 白名单错误
            try:
                error_data = response.json()
                if error_data.get('errcode') == 40164:
                    logger.error("[wechatmp] ⚠️ IP 白名单错误！请检查:")
                    logger.error("[wechatmp]   1. 服务器公网 IP 是否已添加到微信公众平台")
                    logger.error("[wechatmp]   2. 配置是否已生效（通常需要 5-10 分钟）")
                    logger.error("[wechatmp]   3. 运行 diagnose_ip_issue.py 脚本进行诊断")
                    return "图片处理失败: IP 不在微信公众平台白名单中，请联系管理员"
            except:
                pass

            return f"图片处理失败，服务器返回错误: {response.status_code}"

    except Exception as e:
        logger.exception(f"[wechatmp] Error calling remote image API: {e}")
        return f"图片处理出错: {str(e)}"
    finally:
        # 清理临时文件
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
                logger.debug(f"[wechatmp] Removed temp image file: {image_path}")
        except Exception as e:
            logger.warning(f"[wechatmp] Failed to remove temp file: {e}")


# This class is instantiated once per query
class Query:
    def GET(self):
        return verify_server(web.input())

    def POST(self):
        try:
            args = web.input()
            verify_server(args)
            request_time = time.time()
            channel = WechatMPChannel()
            message = web.data()
            encrypt_func = lambda x: x
            if args.get("encrypt_type") == "aes":
                logger.debug("[wechatmp] Receive encrypted post data:\n" + message.decode("utf-8"))
                if not channel.crypto:
                    raise Exception("Crypto not initialized, Please set wechatmp_aes_key in config.json")
                message = channel.crypto.decrypt_message(message, args.msg_signature, args.timestamp, args.nonce)
                encrypt_func = lambda x: channel.crypto.encrypt_message(x, args.nonce, args.timestamp)
            else:
                logger.debug("[wechatmp] Receive post data:\n" + message.decode("utf-8"))
            msg = parse_message(message)
            if msg.type in ["text", "voice", "image"]:
                wechatmp_msg = WeChatMPMessage(msg, client=channel.client)
                from_user = wechatmp_msg.from_user_id
                content = wechatmp_msg.content
                message_id = wechatmp_msg.msg_id

                supported = True
                if "【收到不支持的消息类型，暂无法显示】" in content:
                    supported = False  # not supported, used to refresh

                # New request
                if (
                    channel.cache_dict.get(from_user) is None
                    and from_user not in channel.running
                    or content.startswith("#")
                    and message_id not in channel.request_cnt  # insert the godcmd
                ):
                    # 检查是否启用图片API功能
                    enable_image_api = conf().get("enable_image_api", False)
                    require_trigger_keyword = conf().get("image_api_require_keyword", True)  # 是否需要触发关键词
                    trigger_keywords = conf().get("image_api_trigger_keywords", ["解析题目", "解题", "分析题目"])  # 触发关键词列表

                    # 处理文本消息中的触发关键词
                    if enable_image_api and require_trigger_keyword and msg.type == "text":
                        # 检查是否包含触发关键词
                        if any(keyword in content for keyword in trigger_keywords):
                            logger.info(f"[wechatmp] User {from_user} triggered image API with keyword in: {content}")
                            # 设置用户状态为等待图片
                            channel.user_session_state[from_user] = {
                                "state": "waiting_image",
                                "trigger_time": time.time(),
                                "original_message": content
                            }
                            # 提示用户上传图片，直接返回
                            prompt_text = conf().get("image_api_prompt", "请上传需要解析的题目图片📷")
                            logger.info(f"[wechatmp] Set user {from_user} to waiting_image state, sending prompt")
                            replyPost = create_reply(prompt_text, msg)
                            return encrypt_func(replyPost.render())

                    # 处理图片消息
                    if msg.type == "image" and enable_image_api:
                        # 检查用户是否处于等待图片状态
                        user_state = channel.user_session_state.get(from_user)

                        # 如果需要触发关键词，检查用户状态
                        if require_trigger_keyword:
                            if user_state and user_state.get("state") == "waiting_image":
                                # 检查状态是否过期（默认5分钟）
                                state_timeout = conf().get("image_api_state_timeout", 300)  # 秒
                                if time.time() - user_state.get("trigger_time", 0) > state_timeout:
                                    logger.info(f"[wechatmp] User {from_user} image API state expired")
                                    # 清除过期状态
                                    channel.user_session_state.pop(from_user, None)
                                    # 提示用户重新发送触发词，直接返回
                                    reply_text = "会话已超时，请重新发送触发指令（如：解析题目）"
                                    replyPost = create_reply(reply_text, msg)
                                    return encrypt_func(replyPost.render())
                                else:
                                    # 状态有效，处理图片
                                    logger.info(f"[wechatmp] Received image from {from_user}, calling remote API")
                                    channel.running.add(from_user)

                                    # 下载图片到本地
                                    wechatmp_msg.prepare()
                                    image_path = content  # content是图片的本地路径

                                    # 调用远端API处理图片
                                    subject = conf().get("image_api_subject", "数学")
                                    grade = conf().get("image_api_grade", "初中")
                                    api_result = call_remote_image_api(image_path, subject=subject, grade=grade)

                                    # 将结果缓存，准备返回给用户
                                    channel.cache_dict[from_user].append(("text", api_result))
                                    channel.running.remove(from_user)

                                    # 清除用户状态
                                    channel.user_session_state.pop(from_user, None)

                                    # 不再走正常的消息处理流程
                                    logger.info(f"[wechatmp] Image API result cached for {from_user}")
                            else:
                                # 用户没有先发送触发词，提示用户，直接返回
                                logger.info(f"[wechatmp] User {from_user} sent image without trigger keyword")
                                trigger_hint = "、".join(trigger_keywords)
                                reply_text = f"请先发送触发指令（如：{trigger_hint}），然后再上传图片"
                                replyPost = create_reply(reply_text, msg)
                                return encrypt_func(replyPost.render())
                        else:
                            # 不需要触发关键词，直接处理图片
                            logger.info(f"[wechatmp] Received image from {from_user}, calling remote API (no keyword required)")
                            channel.running.add(from_user)

                            # 下载图片到本地
                            wechatmp_msg.prepare()
                            image_path = content  # content是图片的本地路径

                            # 调用远端API处理图片
                            subject = conf().get("image_api_subject", "数学")
                            grade = conf().get("image_api_grade", "初中")
                            api_result = call_remote_image_api(image_path, subject=subject, grade=grade)

                            # 将结果缓存，准备返回给用户
                            channel.cache_dict[from_user].append(("text", api_result))
                            channel.running.remove(from_user)

                            # 清除用户状态（如果有的话）
                            channel.user_session_state.pop(from_user, None)

                            # 不再走正常的消息处理流程
                            logger.info(f"[wechatmp] Image API result cached for {from_user}")

                    # 如果上面的特殊处理都没有执行，走正常流程
                    if channel.cache_dict.get(from_user) is None and from_user not in channel.running:
                        # The first query begin
                        if msg.type == "voice" and wechatmp_msg.ctype == ContextType.TEXT and conf().get("voice_reply_voice", False):
                            context = channel._compose_context(wechatmp_msg.ctype, content, isgroup=False, desire_rtype=ReplyType.VOICE, msg=wechatmp_msg)
                        else:
                            context = channel._compose_context(wechatmp_msg.ctype, content, isgroup=False, msg=wechatmp_msg)
                        logger.debug("[wechatmp] context: {} {} {}".format(context, wechatmp_msg, supported))

                        if supported and context:
                            channel.running.add(from_user)
                            channel.produce(context)
                        else:
                            trigger_prefix = conf().get("single_chat_prefix", [""])[0]
                            if trigger_prefix or not supported:
                                if trigger_prefix:
                                    reply_text = textwrap.dedent(
                                        f"""\
                                        请输入'{trigger_prefix}'接你想说的话跟我说话。
                                        例如:
                                        {trigger_prefix}你好，很高兴见到你。"""
                                    )
                                else:
                                    reply_text = textwrap.dedent(
                                        """\
                                        你好，很高兴见到你。
                                        请跟我说话吧。"""
                                    )
                            else:
                                logger.error(f"[wechatmp] unknown error")
                                reply_text = textwrap.dedent(
                                    """\
                                    未知错误，请稍后再试"""
                                )

                            replyPost = create_reply(reply_text, msg)
                            return encrypt_func(replyPost.render())

                # Wechat official server will request 3 times (5 seconds each), with the same message_id.
                # Because the interval is 5 seconds, here assumed that do not have multithreading problems.
                request_cnt = channel.request_cnt.get(message_id, 0) + 1
                channel.request_cnt[message_id] = request_cnt
                logger.info(
                    "[wechatmp] Request {} from {} {} {}:{}\n{}".format(
                        request_cnt, from_user, message_id, web.ctx.env.get("REMOTE_ADDR"), web.ctx.env.get("REMOTE_PORT"), content
                    )
                )

                task_running = True
                waiting_until = request_time + 4
                while time.time() < waiting_until:
                    if from_user in channel.running:
                        time.sleep(0.1)
                    else:
                        task_running = False
                        break

                reply_text = ""
                if task_running:
                    if request_cnt < 3:
                        # waiting for timeout (the POST request will be closed by Wechat official server)
                        time.sleep(2)
                        # and do nothing, waiting for the next request
                        return "success"
                    else:  # request_cnt == 3:
                        # return timeout message
                        reply_text = "【正在思考中，回复任意文字尝试获取回复】"
                        replyPost = create_reply(reply_text, msg)
                        return encrypt_func(replyPost.render())

                # reply is ready
                channel.request_cnt.pop(message_id)

                # no return because of bandwords or other reasons
                if from_user not in channel.cache_dict and from_user not in channel.running:
                    return "success"

                # Only one request can access to the cached data
                try:
                    (reply_type, reply_content) = channel.cache_dict[from_user].pop(0)
                    if not channel.cache_dict[from_user]:  # If popping the message makes the list empty, delete the user entry from cache
                        del channel.cache_dict[from_user]
                except IndexError:
                    return "success"

                if reply_type == "text":
                    if len(reply_content.encode("utf8")) <= MAX_UTF8_LEN:
                        reply_text = reply_content
                    else:
                        continue_text = "\n【未完待续，回复任意文字以继续】"
                        splits = split_string_by_utf8_length(
                            reply_content,
                            MAX_UTF8_LEN - len(continue_text.encode("utf-8")),
                            max_split=1,
                        )
                        reply_text = splits[0] + continue_text
                        channel.cache_dict[from_user].append(("text", splits[1]))

                    logger.info(
                        "[wechatmp] Request {} do send to {} {}: {}\n{}".format(
                            request_cnt,
                            from_user,
                            message_id,
                            content,
                            reply_text,
                        )
                    )
                    replyPost = create_reply(reply_text, msg)
                    return encrypt_func(replyPost.render())

                elif reply_type == "voice":
                    media_id = reply_content
                    asyncio.run_coroutine_threadsafe(channel.delete_media(media_id), channel.delete_media_loop)
                    logger.info(
                        "[wechatmp] Request {} do send to {} {}: {} voice media_id {}".format(
                            request_cnt,
                            from_user,
                            message_id,
                            content,
                            media_id,
                        )
                    )
                    replyPost = VoiceReply(message=msg)
                    replyPost.media_id = media_id
                    return encrypt_func(replyPost.render())

                elif reply_type == "image":
                    media_id = reply_content
                    asyncio.run_coroutine_threadsafe(channel.delete_media(media_id), channel.delete_media_loop)
                    logger.info(
                        "[wechatmp] Request {} do send to {} {}: {} image media_id {}".format(
                            request_cnt,
                            from_user,
                            message_id,
                            content,
                            media_id,
                        )
                    )
                    replyPost = ImageReply(message=msg)
                    replyPost.media_id = media_id
                    return encrypt_func(replyPost.render())

            elif msg.type == "event":
                logger.info("[wechatmp] Event {} from {}".format(msg.event, msg.source))
                if msg.event in ["subscribe", "subscribe_scan"]:
                    reply_text = subscribe_msg()
                    if reply_text:
                        replyPost = create_reply(reply_text, msg)
                        return encrypt_func(replyPost.render())
                else:
                    return "success"
            else:
                logger.info("暂且不处理")
            return "success"
        except Exception as exc:
            logger.exception(exc)
            return exc
