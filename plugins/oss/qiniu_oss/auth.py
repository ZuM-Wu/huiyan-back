"""七牛对象存储插件的权限树。"""


def permission_tree() -> list[dict]:
    """返回管理端文件操作权限树。"""
    return [{
        "title": "七牛对象存储",
        "code": "qiniu_oss:*",
        "url": "/admin/system?tab=storage",
        "children": [
            {"title": "配置与连通检测", "code": "qiniu_oss:config"},
            {"title": "浏览文件", "code": "qiniu_oss:list"},
            {"title": "上传文件", "code": "qiniu_oss:upload"},
            {"title": "访问地址", "code": "qiniu_oss:access"},
            {"title": "删除文件", "code": "qiniu_oss:delete"},
        ],
    }]

