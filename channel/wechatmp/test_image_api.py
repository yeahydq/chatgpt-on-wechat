#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试图片API功能的示例脚本
用于验证远端API是否正常工作
"""

import base64
import requests
import json
import sys


def test_image_api(image_path, api_url, subject="数学", grade="初中"):
    """
    测试图片API
    :param image_path: 测试图片的路径
    :param api_url: API的URL地址
    :param subject: 科目
    :param grade: 年级
    """
    print(f"正在测试图片API...")
    print(f"API地址: {api_url}")
    print(f"图片路径: {image_path}")
    print(f"科目: {subject}")
    print(f"年级: {grade}")
    print("-" * 50)
    
    try:
        # 读取图片并转换为base64
        with open(image_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        
        print(f"图片已读取，base64长度: {len(image_data)}")
        
        # 构建请求数据
        payload = {
            "image_data": image_data,
            "question_content": "",
            "subject": subject,
            "grade": grade
        }
        
        # 设置请求头
        headers = {
            'Content-Type': 'application/json',
        }
        
        print("正在发送请求...")
        
        # 发送POST请求
        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=60
        )
        
        print(f"响应状态码: {response.status_code}")
        print("-" * 50)
        
        if response.status_code == 200:
            result = response.json()
            print("API响应成功！")
            print("响应内容:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            
            # 提取结果
            if isinstance(result, dict):
                if result.get('success') or result.get('result'):
                    final_result = result.get('result', result.get('answer', str(result)))
                    print("-" * 50)
                    print("提取的结果:")
                    print(final_result)
                else:
                    error_msg = result.get('error', result.get('message', '未知错误'))
                    print(f"API返回错误: {error_msg}")
            else:
                print(f"结果: {result}")
        else:
            print(f"API返回错误状态码: {response.status_code}")
            print(f"错误信息: {response.text}")
            
    except FileNotFoundError:
        print(f"错误: 找不到图片文件 {image_path}")
    except requests.exceptions.Timeout:
        print("错误: 请求超时")
    except requests.exceptions.ConnectionError:
        print(f"错误: 无法连接到API服务器 {api_url}")
    except Exception as e:
        print(f"错误: {str(e)}")
        import traceback
        traceback.print_exc()


def main():
    """主函数"""
    print("=" * 50)
    print("图片API测试工具")
    print("=" * 50)
    
    # 从命令行参数获取配置，或使用默认值
    if len(sys.argv) < 3:
        print("\n使用方法:")
        print(f"  python {sys.argv[0]} <图片路径> <API地址> [科目] [年级]")
        print("\n示例:")
        print(f"  python {sys.argv[0]} test.jpg http://localhost:8000/api/analyze-answer 数学 初中")
        print("\n或者修改下面的默认配置后直接运行:")
        print("-" * 50)
        
        # 默认配置（可以修改这里进行测试）
        image_path = "test_image.jpg"  # 修改为你的测试图片路径
        api_url = "http://localhost:8000/api/analyze-answer"  # 修改为你的API地址
        subject = "数学"
        grade = "初中"
    else:
        image_path = sys.argv[1]
        api_url = sys.argv[2]
        subject = sys.argv[3] if len(sys.argv) > 3 else "数学"
        grade = sys.argv[4] if len(sys.argv) > 4 else "初中"
    
    test_image_api(image_path, api_url, subject, grade)
    print("\n" + "=" * 50)
    print("测试完成")
    print("=" * 50)


if __name__ == "__main__":
    main()

