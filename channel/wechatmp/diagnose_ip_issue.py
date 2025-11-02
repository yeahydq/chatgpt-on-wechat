#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号 IP 白名单问题诊断脚本
用于快速诊断和解决 IP 白名单相关的问题
"""

import requests
import json
import sys
from urllib.parse import urljoin

def get_public_ip():
    """获取服务器的公网 IP"""
    print("\n[1] 获取服务器公网 IP...")
    try:
        # 尝试多个 IP 查询服务
        services = [
            'https://api.ipify.org?format=json',
            'https://checkip.amazonaws.com',
            'https://icanhazip.com',
        ]
        
        for service in services:
            try:
                response = requests.get(service, timeout=5)
                if response.status_code == 200:
                    if 'json' in service:
                        ip = response.json().get('ip')
                    else:
                        ip = response.text.strip()
                    print(f"✓ 公网 IP: {ip}")
                    return ip
            except:
                continue
        
        print("✗ 无法获取公网 IP，请检查网络连接")
        return None
    except Exception as e:
        print(f"✗ 错误: {e}")
        return None

def check_wechat_api_access(app_id, app_secret):
    """检查是否能访问微信 API"""
    print("\n[2] 检查微信 API 访问...")
    try:
        url = 'https://api.weixin.qq.com/cgi-bin/token'
        params = {
            'grant_type': 'client_credential',
            'appid': app_id,
            'secret': app_secret
        }
        
        response = requests.get(url, params=params, timeout=10)
        result = response.json()
        
        if 'access_token' in result:
            print(f"✓ 微信 API 访问成功")
            print(f"  access_token: {result['access_token'][:20]}...")
            return True
        elif 'errcode' in result:
            errcode = result.get('errcode')
            errmsg = result.get('errmsg')
            print(f"✗ 微信 API 错误 (错误码: {errcode})")
            print(f"  错误信息: {errmsg}")
            
            if errcode == 40164:
                print("  → 这是 IP 白名单问题！请检查你的 IP 是否已添加到微信公众平台")
            elif errcode == 40001:
                print("  → 凭证无效，请检查 app_id 和 app_secret")
            
            return False
        else:
            print(f"✗ 未知错误: {result}")
            return False
            
    except requests.exceptions.Timeout:
        print("✗ 请求超时，可能是网络问题")
        return False
    except Exception as e:
        print(f"✗ 错误: {e}")
        return False

def check_image_api_access(image_api_url):
    """检查是否能访问远端图片 API"""
    print("\n[3] 检查远端图片 API 访问...")
    
    if not image_api_url:
        print("⚠ 未配置 image_api_url，跳过此检查")
        return None
    
    try:
        # 发送测试请求
        test_payload = {
            "image_data": "test",
            "question_content": "test",
            "subject": "数学",
            "grade": "初中"
        }
        
        response = requests.post(
            image_api_url,
            json=test_payload,
            timeout=10
        )
        
        print(f"✓ 可以访问远端 API")
        print(f"  状态码: {response.status_code}")
        print(f"  响应: {response.text[:100]}...")
        return True
        
    except requests.exceptions.ConnectionError:
        print(f"✗ 无法连接到 API: {image_api_url}")
        print("  可能原因:")
        print("  1. API 服务未启动")
        print("  2. API 地址错误")
        print("  3. 网络连接问题")
        return False
    except requests.exceptions.Timeout:
        print(f"✗ 请求 API 超时")
        return False
    except Exception as e:
        print(f"✗ 错误: {e}")
        return False

def print_diagnostic_report(public_ip, wechat_ok, image_api_ok):
    """打印诊断报告"""
    print("\n" + "="*50)
    print("诊断报告")
    print("="*50)
    
    print(f"\n公网 IP: {public_ip if public_ip else '未获取'}")
    print(f"微信 API: {'✓ 正常' if wechat_ok else '✗ 异常'}")
    print(f"图片 API: {'✓ 正常' if image_api_ok is True else ('✗ 异常' if image_api_ok is False else '⚠ 未检查')}")
    
    print("\n建议:")
    if not public_ip:
        print("1. 检查网络连接")
    
    if not wechat_ok:
        print("1. 确认 app_id 和 app_secret 正确")
        print("2. 将公网 IP 添加到微信公众平台的 IP 白名单")
        print("3. 等待 5-10 分钟让配置生效")
    
    if image_api_ok is False:
        print("1. 检查 image_api_url 配置是否正确")
        print("2. 确认远端 API 服务已启动")
        print("3. 检查网络连接")

def main():
    """主函数"""
    print("微信公众号 IP 白名单问题诊断工具")
    print("="*50)
    
    # 从命令行参数获取配置
    if len(sys.argv) < 3:
        print("\n使用方法:")
        print("  python3 diagnose_ip_issue.py <app_id> <app_secret> [image_api_url]")
        print("\n示例:")
        print("  python3 diagnose_ip_issue.py your_app_id your_app_secret")
        print("  python3 diagnose_ip_issue.py your_app_id your_app_secret http://localhost:8000/api/analyze-answer")
        sys.exit(1)
    
    app_id = sys.argv[1]
    app_secret = sys.argv[2]
    image_api_url = sys.argv[3] if len(sys.argv) > 3 else None
    
    # 执行诊断
    public_ip = get_public_ip()
    wechat_ok = check_wechat_api_access(app_id, app_secret)
    image_api_ok = check_image_api_access(image_api_url)
    
    # 打印报告
    print_diagnostic_report(public_ip, wechat_ok, image_api_ok)
    
    print("\n" + "="*50)
    if public_ip and wechat_ok:
        print("✓ 诊断完成，系统正常")
    else:
        print("✗ 诊断完成，发现问题，请按照建议进行修复")

if __name__ == '__main__':
    main()

