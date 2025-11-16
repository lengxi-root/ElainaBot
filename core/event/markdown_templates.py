#!/usr/bin/env python
# -*- coding: utf-8 -*-

# 模板缓存，避免重复查找
_template_cache = {}

MARKDOWN_TEMPLATES = {
   


    "1": {
        "id": "102321943_1750340771",
        "params": ['text', 'size', 'url', 'size2', 'url2', 'size3', 'url3', 'size4', 'url4', 'text2']
    },
    # 原始模板内容: {{.text}}![{{.size}}]({{.url}})![{{.size2}}]({{.url2}})![{{.size3}}]({{.url3}})![{{.size4}}]({{.url4}}){{.text2}}
    "2": {
        "id": "102321943_1749730968",
        "params": ['img', 'url', 'text', 'text1', 'text2']
    },
    # 原始模板内容: ![{{.img}}]({{.url}})\n{{.text}}\n<qqbot-cmd-input text="{{.text1}}" />\n<qqbot-cmd-input text="{{.text2}}" />
    "3": {
        "id": "102321943_1747856890",
        "params": ['text', 'text2', 'text3']
    },
    # 原始模板内容: {{.text}}\n```\n{{.text2}}\n```\n{{.text3}}
    "4": {
        "id": "102321943_1747062205",
        "params": ['text']
    },
    # 原始模板内容: {{.text}}
    "5": {
        "id": "102321943_1747061997",
        "params": ['px', 'url', 'text']
    },
    # 原始模板内容: ![{{.px}}]({{.url}}){{.text}}

    # 按钮模板ID
    # 按钮ID: 102321943_1753874574 - 签到模板
    # 按钮ID: 102321943_1752737844 - 签到模板
    # 按钮ID: 102321943_1749304479 - 随机图
    # 按钮ID: 102321943_1748704691 - 音乐系列-按钮
    # 按钮ID: 102321943_1748704372 - 菜单系列-按钮
    # 按钮ID: 102321943_1748453868 - 表情包系列
    # 按钮ID: 102321943_1748181537 - 今日系列
    # 按钮ID: 102321943_1748181433 - 群友老婆-1
    # 按钮ID: 102321943_1747856946 - 漂流瓶系列-1
    # 按钮ID: 102321943_1747837000 - 塔罗牌系列
    # 按钮ID: 102321943_1747836634 - 超能力系列
    # 按钮ID: 102321943_1747834486 - 运势系列
    # 按钮ID: 102321943_1744896971 - 图库菜单-小小花火
    # 按钮ID: 102321943_1744283462 - 一言-小小花火
    # 按钮ID: 102321943_1744010653 - 表情包制作-小小花火
    "6": {
        "id": "102134274_1749040268",
        "params": ['text']
    },
    # 原始模板内容: {{.text}}
    "7": {
        "id": "102134274_1748254289",
        "params": ['tag', 'url']
    },
    # 原始模板内容: ![{{.tag}}]({{.url}})
    "8": {
        "id": "102134274_1748253984",
        "params": ['tag', 'url']
    },
    # 原始模板内容: [{{.tag}}]({{.url}})
    "9": {
        "id": "102134274_1732198356",
        "params": ['px', 'url', 'text', 'dj', 'dj2', 'text2', 'dj3', 'dj4', 'tz2', 'url2']
    },
    # 原始模板内容: ![{{.px}}]({{.url}}){{.text}}<qqbot-cmd-input text="{{.dj}}" show="{{.dj2}}" />{{.text2}}<qqbot-cmd-input text="{{.dj3}}" show="{{.dj4}}" />[{{.tz2}}]({{.url2}})
    "10": {
        "id": "102134274_1732196818",
        "params": ['px', 'imageurl', 'text', 'dj', 'text2', 'dj2', 'text3', 'tz2', 'url2']
    },
    # 原始模板内容: ![{{.px}}]({{.imageurl}}){{.text}}<qqbot-cmd-input text="{{.dj}}" />{{.text2}}<qqbot-cmd-input text="{{.dj2}}" />{{.text3}}[{{.tz2}}]({{.url2}})
    "11": {
        "id": "102134274_1730638216",
        "params": ['text', 'tz', 'url', 'px', 'imageurl', 'text2', 'tz2', 'url2']
    },
    # 原始模板内容: {{.text}}[{{.tz}}]({{.url}})![{{.px}}]({{.imageurl}}){{.text2}}[{{.tz2}}]({{.url2}})
    "12": {
        "id": "102134274_1723126491",
        "params": ['text_start', 'img_dec', 'img_url', 'text_end']
    },
    # 原始模板内容: {{.text_start}}![{{.img_dec}}]({{.img_url}}){{.text_end}}

}

def get_template(template_name):
    """获取指定名称的模板配置"""
    # 使用缓存提升查找性能
    if template_name in _template_cache:
        return _template_cache[template_name]
    
    template = MARKDOWN_TEMPLATES.get(template_name)
    if template:
        _template_cache[template_name] = template
    
    return template

def get_all_templates():
    """获取所有模板配置"""
    return MARKDOWN_TEMPLATES

def reload_templates():
    """热加载模板配置（重新导入模块）"""
    try:
        import importlib
        import sys
        if 'core.event.markdown_templates' in sys.modules:
            importlib.reload(sys.modules['core.event.markdown_templates'])
        return True
    except Exception:
        return False 