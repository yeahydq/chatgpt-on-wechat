import asyncio
import time
import requests
import os
import base64
import io
import imghdr
import threading

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

try:
    import markdown2
    HAS_MARKDOWN2 = True
except ImportError:
    HAS_MARKDOWN2 = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import markdown
    HAS_MARKDOWN = True
except ImportError:
    HAS_MARKDOWN = False

try:
    from PIL import ImageFont
    import subprocess
    HAS_LATEX = True
except ImportError:
    HAS_LATEX = False


def extract_and_replace_formulas(text):
    """
    提取文本中的公式（LaTeX 格式），并用占位符替换
    :param text: 包含公式的文本
    :return: (处理后的文本, 公式字典)
    """
    import re

    formulas = {}
    formula_count = 0

    # 处理行内公式 $...$
    def replace_inline_formula(match):
        nonlocal formula_count
        formula = match.group(1)
        placeholder = f"[FORMULA_{formula_count}]"
        formulas[placeholder] = formula
        formula_count += 1
        logger.info(f"[wechatmp] Found inline formula: {formula}")
        return placeholder

    # 处理块级公式 $$...$$
    def replace_block_formula(match):
        nonlocal formula_count
        formula = match.group(1)
        placeholder = f"[FORMULA_{formula_count}]"
        formulas[placeholder] = formula
        formula_count += 1
        logger.info(f"[wechatmp] Found block formula: {formula}")
        return placeholder

    # 替换块级公式（必须在行内公式之前）
    text = re.sub(r'\$\$(.*?)\$\$', replace_block_formula, text, flags=re.DOTALL)

    # 替换行内公式
    text = re.sub(r'\$(.*?)\$', replace_inline_formula, text)

    logger.info(f"[wechatmp] Extracted {len(formulas)} formulas")

    return text, formulas


def markdown_to_image(markdown_text, output_path=None):
    """
    将 Markdown 文本转换为图片（使用 markdown2 + playwright）
    :param markdown_text: Markdown 文本内容
    :param output_path: 输出图片路径（如果为None，则保存到临时目录）
    :return: 图片路径
    """
    if not HAS_MARKDOWN2 or not HAS_PLAYWRIGHT:
        logger.warning(f"[wechatmp] markdown2={HAS_MARKDOWN2} or playwright={HAS_PLAYWRIGHT} not installed, cannot convert markdown to image")
        return None

    try:
        logger.info(f"[wechatmp] Converting markdown to image using markdown2 + playwright, text length: {len(markdown_text)}")

        # 规范化换行符
        text = markdown_text
        text = text.replace('\r\n', '\n')
        text = text.replace('\r', '\n')
        text = text.replace('\\n', '\n')
        text = text.replace('\\r\\n', '\n')

        # 提取公式，用占位符替换
        text, formulas = extract_and_replace_formulas(text)
        if formulas:
            logger.info(f"[wechatmp] Extracted {len(formulas)} formulas, will be shown as placeholders")

        # 使用 markdown2 将 markdown 转换为 HTML
        html_content = markdown2.markdown(text, extras=['fenced-code-blocks', 'tables', 'strikethrough'])

        # 添加 CSS 样式
        html_with_style = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
                    font-size: 16px;
                    line-height: 1.8;
                    color: #333;
                    background-color: #fff;
                    padding: 30px;
                    margin: 0;
                    -webkit-font-smoothing: antialiased;
                    -moz-osx-font-smoothing: grayscale;
                }}
                h1, h2, h3, h4, h5, h6 {{
                    margin: 15px 0 10px 0;
                    font-weight: bold;
                }}
                h1 {{ font-size: 28px; }}
                h2 {{ font-size: 24px; }}
                h3 {{ font-size: 20px; }}
                h4 {{ font-size: 18px; }}
                p {{ margin: 10px 0; }}
                code {{
                    background-color: #f5f5f5;
                    padding: 3px 8px;
                    border-radius: 4px;
                    font-family: 'Courier New', monospace;
                    font-size: 15px;
                }}
                pre {{
                    background-color: #f5f5f5;
                    padding: 15px;
                    border-radius: 4px;
                    overflow-x: auto;
                    font-size: 14px;
                    line-height: 1.5;
                }}
                blockquote {{
                    border-left: 4px solid #ddd;
                    margin: 15px 0;
                    padding-left: 15px;
                    color: #666;
                }}
                ul, ol {{
                    margin: 10px 0;
                    padding-left: 30px;
                }}
                li {{ margin: 6px 0; }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 15px 0;
                    font-size: 15px;
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 10px;
                    text-align: left;
                }}
                th {{ background-color: #f5f5f5; font-weight: bold; }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """

        logger.info(f"[wechatmp] Converted markdown to HTML, length: {len(html_with_style)}")

        # 生成输出路径
        if output_path is None:
            output_path = f"tmp/markdown_{int(time.time())}.png"

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 使用 playwright 将 HTML 转换为图片
        logger.info(f"[wechatmp] Converting HTML to image using playwright: {output_path}")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                # 使用高分辨率和设备像素比提高清晰度
                page = browser.new_page(
                    viewport={"width": 2000, "height": 1000},
                    device_scale_factor=1.5  # 1.5倍分辨率
                )
                page.set_content(html_with_style)

                # 等待内容加载
                page.wait_for_load_state('networkidle')

                # 获取实际内容高度
                content_height = page.evaluate('document.body.scrollHeight')
                page.set_viewport_size({"width": 2000, "height": int(content_height)})

                # 截图
                page.screenshot(path=output_path, full_page=True)
                browser.close()

                logger.info(f"[wechatmp] Markdown converted to image: {output_path}")
                return output_path
        except Exception as e:
            logger.error(f"[wechatmp] Playwright conversion failed: {e}")
            return None

    except Exception as e:
        logger.exception(f"[wechatmp] Error converting markdown to image: {e}")
        return None


def compress_image(image_path, max_size_mb=3, quality=95, max_width=2000, max_height=2000):
    """
    压缩图片以减小文件大小
    :param image_path: 原始图片路径
    :param max_size_mb: 目标最大大小（MB）
    :param quality: JPEG 质量（1-100）
    :param max_width: 最大宽度
    :param max_height: 最大高度
    :return: 压缩后的图片数据（字节）
    """
    logger.info(f"[wechatmp] compress_image called with path: {image_path}")
    logger.info(f"[wechatmp] HAS_PIL: {HAS_PIL}")

    if not HAS_PIL:
        logger.warning("[wechatmp] PIL not installed, using original image")
        with open(image_path, 'rb') as f:
            return f.read()

    try:
        logger.info(f"[wechatmp] Opening image: {image_path}")
        # 打开图片
        img = Image.open(image_path)
        logger.info(f"[wechatmp] Image opened, mode: {img.mode}, size: {img.size}")

        # 转换为 RGB（处理 RGBA 等格式）
        if img.mode in ('RGBA', 'LA', 'P'):
            logger.info(f"[wechatmp] Converting image from {img.mode} to RGB")
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = rgb_img

        # 缩小尺寸
        logger.info(f"[wechatmp] Resizing image to max {max_width}x{max_height}")
        img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        logger.info(f"[wechatmp] Image resized to: {img.size}")

        # 压缩到目标大小
        max_size_bytes = max_size_mb * 1024 * 1024
        current_quality = quality
        logger.info(f"[wechatmp] Starting compression, target size: {max_size_bytes} bytes, initial quality: {current_quality}%")

        while current_quality > 70:
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=current_quality, optimize=True)
            compressed_data = buffer.getvalue()
            logger.info(f"[wechatmp] Quality {current_quality}%: {len(compressed_data)} bytes")

            if len(compressed_data) <= max_size_bytes:
                logger.info(f"[wechatmp] ✅ Image compressed: {os.path.getsize(image_path)} → {len(compressed_data)} bytes (quality: {current_quality}%)")
                return compressed_data

            current_quality -= 2

        # 如果仍然超过大小，返回质量70的版本
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=70, optimize=True)
        compressed_data = buffer.getvalue()
        logger.warning(f"[wechatmp] ⚠️ Image compressed to quality 70: {len(compressed_data)} bytes")
        return compressed_data

    except Exception as e:
        logger.error(f"[wechatmp] ❌ Failed to compress image: {e}, using original")
        import traceback
        logger.error(f"[wechatmp] Traceback: {traceback.format_exc()}")
        with open(image_path, 'rb') as f:
            return f.read()


def process_image_api_async(channel, from_user, image_path, subject="数学", grade="初中"):
    """
    在后台线程中异步处理图片API调用
    :param channel: WeChat 频道对象
    :param from_user: 用户ID
    :param image_path: 图片路径
    :param subject: 科目
    :param grade: 年级
    """
    try:
        logger.info(f"[wechatmp] Starting async image processing for {from_user}")

        # 调用API
        api_result = call_remote_image_api(image_path, subject=subject, grade=grade)

        # 缓存结果
        if isinstance(api_result, tuple) and len(api_result) == 2:
            # 返回图片 + 文字
            text_content, image_path_result = api_result

            # 上传图片到微信服务器并获取 media_id
            try:
                if os.path.exists(image_path_result):
                    logger.info(f"[wechatmp] Uploading markdown image to WeChat: {image_path_result}")
                    with open(image_path_result, 'rb') as f:
                        image_type = imghdr.what(image_path_result)
                        filename = f"markdown-{int(time.time())}.{image_type}"
                        content_type = f"image/{image_type}"
                        response = channel.client.material.add("image", (filename, f, content_type))
                        media_id = response.get("media_id")
                        logger.info(f"[wechatmp] Markdown image uploaded, media_id: {media_id}")

                    # 删除本地临时文件
                    try:
                        os.remove(image_path_result)
                        logger.info(f"[wechatmp] Deleted temporary markdown image: {image_path_result}")
                    except Exception as e:
                        logger.warning(f"[wechatmp] Failed to delete temporary markdown image: {e}")

                    # 缓存 media_id 和文字
                    channel.cache_dict[from_user].append(("image", media_id))
                    channel.cache_dict[from_user].append(("text", text_content))
                    logger.info(f"[wechatmp] Async: Cached image (media_id) + text result for {from_user}")
                else:
                    logger.warning(f"[wechatmp] Markdown image file not found: {image_path_result}")
                    channel.cache_dict[from_user].append(("text", text_content))
            except Exception as e:
                logger.error(f"[wechatmp] Failed to upload markdown image: {e}")
                import traceback
                logger.error(f"[wechatmp] Traceback: {traceback.format_exc()}")
                channel.cache_dict[from_user].append(("text", text_content))
        else:
            # 只返回文字
            channel.cache_dict[from_user].append(("text", api_result))
            logger.info(f"[wechatmp] Async: Cached text result for {from_user}")

        logger.info(f"[wechatmp] Async image processing completed for {from_user}")
    except Exception as e:
        logger.error(f"[wechatmp] Error in async image processing for {from_user}: {e}")
        channel.cache_dict[from_user].append(("text", f"图片处理出错: {str(e)}"))
    finally:
        # 移除运行状态
        if from_user in channel.running:
            channel.running.remove(from_user)


def call_remote_image_api(image_path, question_content="帮我解析一下题目", subject="数学", grade="初中"):
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
        logger.info(f"[wechatmp] Image path type: {type(image_path)}")
        logger.info(f"[wechatmp] Image file exists: {os.path.exists(image_path)}")
        original_size = os.path.getsize(image_path) if os.path.exists(image_path) else 'N/A'
        logger.info(f"[wechatmp] Image file size: {original_size} bytes")

        # 压缩图片以减小请求体大小
        logger.info("[wechatmp] Compressing image...")
        compressed_image_data = compress_image(image_path, max_size_mb=2, quality=90)
        logger.info(f"[wechatmp] Image compressed: {original_size} → {len(compressed_image_data)} bytes")

        # 转换为base64
        image_data = base64.b64encode(compressed_image_data).decode('utf-8')

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

        # 记录请求详情（用于调试）
        logger.info(f"[wechatmp] ========== API Request Details ==========")
        logger.info(f"[wechatmp] URL: {api_url}")
        logger.info(f"[wechatmp] Method: POST")
        logger.info(f"[wechatmp] Headers: {headers}")
        logger.info(f"[wechatmp] Payload keys: {list(payload.keys())}")
        logger.info(f"[wechatmp] Payload (without image_data): {{'image_data': '<base64 data, length={len(image_data)}>', 'question_content': '{payload.get('question_content')}', 'subject': '{payload.get('subject')}', 'grade': '{payload.get('grade')}'}}")
        logger.info(f"[wechatmp] Timeout: 120 seconds")
        logger.info(f"[wechatmp] ==========================================")

        # 发送POST请求到远端API
        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=120  # 图片分析可能需要较长时间
        )

        if response.status_code == 200:
            # 解析API返回结果
            result = response.json()
            logger.info(f"[wechatmp] Image API response: {result}")

            # 根据实际API返回格式提取结果
            # 假设返回格式为 {"result": "分析结果", "success": true}
            if isinstance(result, dict):
                if result.get('success') or result.get('result'):
                    # 提取分析结果
                    analysis_text = None

                    # 尝试从不同的字段中提取分析结果
                    if result.get('data') and isinstance(result['data'], dict):
                        analysis_text = result['data'].get('analysis', result.get('result', result.get('answer')))
                    else:
                        analysis_text = result.get('result', result.get('answer', str(result)))

                    if not analysis_text:
                        analysis_text = str(result)

                    # 确保文本是字符串类型
                    if not isinstance(analysis_text, str):
                        analysis_text = str(analysis_text)

                    logger.info(f"[wechatmp] Analysis text type: {type(analysis_text)}, length: {len(analysis_text)}")
                    logger.info(f"[wechatmp] Analysis text preview: {repr(analysis_text[:100])}")

                    # 检查是否启用了 markdown 转图片功能（默认启用）
                    enable_markdown_image = conf().get("enable_markdown_image", True)

                    logger.info(f"[wechatmp] Image conversion check: enable_markdown_image={enable_markdown_image}, HAS_MARKDOWN2={HAS_MARKDOWN2}, HAS_PLAYWRIGHT={HAS_PLAYWRIGHT}")

                    if enable_markdown_image and HAS_MARKDOWN2 and HAS_PLAYWRIGHT:
                        logger.info("[wechatmp] Converting analysis result to image...")
                        # 将分析结果转换为图片
                        image_path = markdown_to_image(analysis_text)

                        if image_path and os.path.exists(image_path):
                            # 返回一个包含文字和图片的结构
                            # 格式：(text_content, image_path)
                            logger.info(f"[wechatmp] Analysis converted to image: {image_path}")
                            return (analysis_text, image_path)
                        else:
                            logger.warning("[wechatmp] Failed to convert to image, returning text only")
                            return analysis_text
                    else:
                        logger.warning(f"[wechatmp] Skipping image conversion: enable={enable_markdown_image}, markdown2={HAS_MARKDOWN2}, playwright={HAS_PLAYWRIGHT}")
                        return analysis_text
                else:
                    error_msg = result.get('error', result.get('message', '未知错误'))
                    return f"图片分析失败: {error_msg}"
            else:
                return str(result)
        else:
            logger.error(f"[wechatmp] ========== API Response Error ==========")
            logger.error(f"[wechatmp] Status Code: {response.status_code}")
            logger.error(f"[wechatmp] Response Headers: {dict(response.headers)}")
            logger.error(f"[wechatmp] Response Body: {response.text}")
            logger.error(f"[wechatmp] Request URL: {api_url}")
            logger.error(f"[wechatmp] Request Method: POST")
            logger.error(f"[wechatmp] Request Headers: {headers}")
            logger.error(f"[wechatmp] Request Payload (without image_data): {{'image_data': '<base64 data, length={len(image_data)}>', 'question_content': '{payload.get('question_content')}', 'subject': '{payload.get('subject')}', 'grade': '{payload.get('grade')}'}}")
            logger.error(f"[wechatmp] ==========================================")

            # 检查是否是请求体过大错误
            if response.status_code == 413:
                logger.error("[wechatmp] ⚠️ 请求体过大错误（413）！")
                logger.error("[wechatmp] 解决方案:")
                logger.error("[wechatmp]   1. 图片已自动压缩，但仍然超过限制")
                logger.error("[wechatmp]   2. 请检查 API 服务器的请求体大小限制")
                logger.error("[wechatmp]   3. 如果使用 nginx，增加 client_max_body_size 配置")
                logger.error("[wechatmp]   4. 或者在 API 服务器端增加请求体大小限制")
                return "图片处理失败: 请求体过大，请联系管理员增加服务器限制"

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
                            prompt_text = conf().get("image_api_prompt", "请上传需要解析的题目图片， 由于识别耗时，请多次查询结果")
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
                                    logger.info(f"[wechatmp] Before prepare() - content type: {type(content)}, content: {content}")
                                    logger.info(f"[wechatmp] Before prepare() - ctype: {wechatmp_msg.ctype}")

                                    wechatmp_msg.prepare()

                                    logger.info(f"[wechatmp] After prepare() - content type: {type(content)}, content: {content}")
                                    logger.info(f"[wechatmp] After prepare() - wechatmp_msg.content: {wechatmp_msg.content}")
                                    logger.info(f"[wechatmp] Image file exists: {os.path.exists(wechatmp_msg.content)}")

                                    # ⚠️ 重要：使用 wechatmp_msg.content 而不是 content 变量
                                    # 因为 content 是在 prepare() 之前赋值的，不会被更新
                                    image_path = wechatmp_msg.content  # 使用 wechatmp_msg.content

                                    # 在后台线程中异步处理图片API调用（避免超时）
                                    subject = conf().get("image_api_subject", "数学")
                                    grade = conf().get("image_api_grade", "初中")

                                    # 启动后台线程处理
                                    thread = threading.Thread(
                                        target=process_image_api_async,
                                        args=(channel, from_user, image_path, subject, grade),
                                        daemon=True
                                    )
                                    thread.start()

                                    # 清除用户状态
                                    channel.user_session_state.pop(from_user, None)

                                    # 立即返回"正在分析中"提示（不等待API返回）
                                    reply_text = "✅ 已收到图片，正在分析中...请稍候"
                                    replyPost = create_reply(reply_text, msg)
                                    return encrypt_func(replyPost.render())
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
                            logger.info(f"[wechatmp] Before prepare() - content type: {type(content)}, content: {content}")
                            logger.info(f"[wechatmp] Before prepare() - ctype: {wechatmp_msg.ctype}")

                            wechatmp_msg.prepare()

                            logger.info(f"[wechatmp] After prepare() - content type: {type(content)}, content: {content}")
                            logger.info(f"[wechatmp] After prepare() - wechatmp_msg.content: {wechatmp_msg.content}")
                            logger.info(f"[wechatmp] Image file exists: {os.path.exists(wechatmp_msg.content)}")

                            # ⚠️ 重要：使用 wechatmp_msg.content 而不是 content 变量
                            # 因为 content 是在 prepare() 之前赋值的，不会被更新
                            image_path = wechatmp_msg.content  # 使用 wechatmp_msg.content

                            # 在后台线程中异步处理图片API调用（避免超时）
                            subject = conf().get("image_api_subject", "数学")
                            grade = conf().get("image_api_grade", "初中")

                            # 启动后台线程处理
                            thread = threading.Thread(
                                target=process_image_api_async,
                                args=(channel, from_user, image_path, subject, grade),
                                daemon=True
                            )
                            thread.start()

                            # 清除用户状态（如果有的话）
                            channel.user_session_state.pop(from_user, None)

                            # 立即返回"正在分析中"提示（不等待API返回）
                            reply_text = "✅ 已收到图片，正在分析中...请稍候"
                            replyPost = create_reply(reply_text, msg)
                            return encrypt_func(replyPost.render())

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
                    logger.info(f"[wechatmp] Cache dict for {from_user}: {channel.cache_dict.get(from_user, [])}")
                    (reply_type, reply_content) = channel.cache_dict[from_user].pop(0)
                    logger.info(f"[wechatmp] Popped from cache: type={reply_type}, content_preview={str(reply_content)[:100]}")
                    if not channel.cache_dict[from_user]:  # If popping the message makes the list empty, delete the user entry from cache
                        del channel.cache_dict[from_user]
                except IndexError:
                    logger.warning(f"[wechatmp] Cache is empty for {from_user}")
                    return "success"

                if reply_type == "text":
                    if len(reply_content.encode("utf8")) <= MAX_UTF8_LEN:
                        reply_text = reply_content
                    else:
                        continue_text = "\n【内容过长，回复任意文字以继续】"
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
                    # reply_content 可能是 (media_id, hint_text) 元组或本地文件路径
                    media_id = None
                    local_image_path = None
                    hint_text = "💡 需要文字版本？请回复：文字"

                    # 检查是否是元组（包含 media_id 和提示文字）
                    if isinstance(reply_content, tuple) and len(reply_content) == 2:
                        media_id, hint_text = reply_content
                        logger.info(f"[wechatmp] Processing image reply with hint, media_id: {media_id}, hint: {hint_text}")
                    else:
                        logger.info(f"[wechatmp] Processing image reply, reply_content: {reply_content}, exists: {os.path.exists(reply_content)}")

                        if os.path.exists(reply_content):
                            # 本地文件路径，需要上传到微信服务器
                            logger.info(f"[wechatmp] Uploading local image to WeChat: {reply_content}")
                            local_image_path = reply_content  # 保存本地路径，稍后删除
                            try:
                                # 检查文件大小
                                file_size = os.path.getsize(reply_content)
                                logger.info(f"[wechatmp] Image file size: {file_size} bytes")

                                with open(reply_content, 'rb') as f:
                                    image_type = imghdr.what(reply_content)
                                    logger.info(f"[wechatmp] Image type: {image_type}")
                                    filename = f"image-{message_id}.{image_type}"
                                    content_type = f"image/{image_type}"
                                    logger.info(f"[wechatmp] Uploading with filename: {filename}, content_type: {content_type}")
                                    response = channel.client.material.add("image", (filename, f, content_type))
                                    logger.info(f"[wechatmp] upload image response: {response}")
                                    media_id = response.get("media_id")
                                    logger.info(f"[wechatmp] image uploaded, receiver {from_user}, media_id {media_id}")
                            except Exception as e:
                                logger.error(f"[wechatmp] Failed to upload image: {e}")
                                import traceback
                                logger.error(f"[wechatmp] Traceback: {traceback.format_exc()}")
                                # 上传失败，返回错误信息
                                reply_text = "图片上传失败，请稍后重试"
                                replyPost = create_reply(reply_text, msg)
                                return encrypt_func(replyPost.render())
                        else:
                            # media_id
                            logger.info(f"[wechatmp] Using media_id directly: {reply_content}")
                            media_id = reply_content
                            asyncio.run_coroutine_threadsafe(channel.delete_media(media_id), channel.delete_media_loop)

                    # 发送图片 + 文字提示
                    if media_id:
                        logger.info(
                            "[wechatmp] Request {} do send to {} {}: {} image media_id {} with hint".format(
                                request_cnt,
                                from_user,
                                message_id,
                                content,
                                media_id,
                            )
                        )

                        # 构建包含图片和文字的 XML 响应
                        xml_response = f"""<xml>
<ToUserName><![CDATA[{msg.source}]]></ToUserName>
<FromUserName><![CDATA[{msg.target}]]></FromUserName>
<CreateTime>{int(time.time())}</CreateTime>
<MsgType><![CDATA[image]]></MsgType>
<Image>
<MediaId><![CDATA[{media_id}]]></MediaId>
</Image>
</xml>"""

                        result = encrypt_func(xml_response)

                        # 发送成功后，删除本地临时文件
                        if local_image_path and os.path.exists(local_image_path):
                            try:
                                os.remove(local_image_path)
                                logger.info(f"[wechatmp] Deleted temporary image after sending: {local_image_path}")
                            except Exception as e:
                                logger.warning(f"[wechatmp] Failed to delete temporary image: {e}")

                        # 缓存提示文字，用户下次发送消息时会收到
                        if from_user not in channel.cache_dict:
                            channel.cache_dict[from_user] = []
                        channel.cache_dict[from_user].append(("text", hint_text))
                        logger.info(f"[wechatmp] Cached hint text for {from_user}: {hint_text}")

                        return result
                    else:
                        logger.error("[wechatmp] Failed to get media_id for image")
                        reply_text = "图片发送失败，请稍后重试"
                        replyPost = create_reply(reply_text, msg)
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
