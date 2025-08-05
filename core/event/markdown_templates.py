#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Markdown模板配置文件
"""

# Markdown模板配置
# 格式: "模板名称": {"id": "模板ID", "params": ["参数1", "参数2", ...]}
MARKDOWN_TEMPLATES = {

    "1": {
        "id": "102321943_1750340771",
        "params": ["text", "size", "url", "size2", "url2", "size3", "url3", "size4", "url4", "text2"]
    },
    
      
    # 可以在这里添加更多模板...或直接在框架内登录开放平台导入
    # "custom_template": {
    #     "id": "模板ID",
    #     "params": ["参数1", "参数2", ...]
    # }
}

def get_template(template_name):
    """获取指定名称的模板配置"""
    return MARKDOWN_TEMPLATES.get(template_name)

def get_all_templates():
    """获取所有模板配置"""
    return MARKDOWN_TEMPLATES 