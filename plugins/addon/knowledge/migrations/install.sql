-- 农业知识库插件建表脚本
-- 由 plugin.install() 读取并执行；使用 IF NOT EXISTS 保证可重复安装

CREATE TABLE IF NOT EXISTS hy_plugin_knowledge_category (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '分类ID',
    parent_id INT NOT NULL DEFAULT 0 COMMENT '父分类ID 0=大类',
    name VARCHAR(50) NOT NULL COMMENT '分类名称',
    sort_order INT NOT NULL DEFAULT 0 COMMENT '排序值 越小越靠前',
    status TINYINT(1) NOT NULL DEFAULT 1 COMMENT '状态 0停用 1启用',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_parent (parent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='农业知识库插件-分类表(二级)';

CREATE TABLE IF NOT EXISTS hy_plugin_knowledge_entry (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '知识条目ID',
    title VARCHAR(200) NOT NULL COMMENT '知识标题 如 小麦白粉病',
    category_id INT NOT NULL DEFAULT 0 COMMENT '所属分类ID 指向子类 允许指向大类',
    crop VARCHAR(200) NOT NULL DEFAULT '' COMMENT '适用作物 逗号分隔标签',
    summary VARCHAR(500) NOT NULL DEFAULT '' COMMENT '摘要',
    cause TEXT COMMENT '发生原因',
    solution TEXT COMMENT '解决方案',
    images TEXT COMMENT '典型图片 JSON数组存/upload/...URL',
    view_count INT NOT NULL DEFAULT 0 COMMENT '浏览次数',
    sort_order INT NOT NULL DEFAULT 0 COMMENT '排序值 越小越靠前',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '最后操作管理员ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_category (category_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='农业知识库插件-知识条目表';

CREATE TABLE IF NOT EXISTS hy_plugin_knowledge_correction (
    id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '勘误ID',
    knowledge_id INT NOT NULL DEFAULT 0 COMMENT '关联知识条目ID',
    farmer_id INT NOT NULL DEFAULT 0 COMMENT '提交农户ID',
    content VARCHAR(1000) NOT NULL DEFAULT '' COMMENT '修正建议',
    status TINYINT(1) NOT NULL DEFAULT 0 COMMENT '状态 0待处理 1已采纳 2已驳回',
    admin_note VARCHAR(500) NOT NULL DEFAULT '' COMMENT '管理员处理备注',
    admin_id INT NOT NULL DEFAULT 0 COMMENT '处理管理员ID',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '提交时间',
    handle_time DATETIME NULL COMMENT '处理时间',
    INDEX idx_knowledge (knowledge_id),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='农业知识库插件-勘误表';

-- 大类种子数据（幂等：按名称+parent_id=0 判重）
INSERT INTO hy_plugin_knowledge_category (name, parent_id, sort_order)
SELECT '病害', 0, 1 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM hy_plugin_knowledge_category WHERE name='病害' AND parent_id=0);
INSERT INTO hy_plugin_knowledge_category (name, parent_id, sort_order)
SELECT '虫害', 0, 2 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM hy_plugin_knowledge_category WHERE name='虫害' AND parent_id=0);
INSERT INTO hy_plugin_knowledge_category (name, parent_id, sort_order)
SELECT '草害', 0, 3 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM hy_plugin_knowledge_category WHERE name='草害' AND parent_id=0);
INSERT INTO hy_plugin_knowledge_category (name, parent_id, sort_order)
SELECT '缺素与营养障碍', 0, 4 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM hy_plugin_knowledge_category WHERE name='缺素与营养障碍' AND parent_id=0);
INSERT INTO hy_plugin_knowledge_category (name, parent_id, sort_order)
SELECT '生理性障碍', 0, 5 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM hy_plugin_knowledge_category WHERE name='生理性障碍' AND parent_id=0);
INSERT INTO hy_plugin_knowledge_category (name, parent_id, sort_order)
SELECT '农事操作', 0, 6 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM hy_plugin_knowledge_category WHERE name='农事操作' AND parent_id=0);
INSERT INTO hy_plugin_knowledge_category (name, parent_id, sort_order)
SELECT '其他', 0, 7 FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM hy_plugin_knowledge_category WHERE name='其他' AND parent_id=0);

-- 病害下预置子类（幂等：按名称 + 父类为"病害"大类判重）
INSERT INTO hy_plugin_knowledge_category (name, parent_id, sort_order)
SELECT '真菌性病害', c.id, 1
FROM (SELECT id FROM hy_plugin_knowledge_category WHERE name='病害' AND parent_id=0 LIMIT 1) c
WHERE NOT EXISTS (
    SELECT 1 FROM hy_plugin_knowledge_category k WHERE k.name='真菌性病害' AND k.parent_id=c.id
);
INSERT INTO hy_plugin_knowledge_category (name, parent_id, sort_order)
SELECT '细菌性病害', c.id, 2
FROM (SELECT id FROM hy_plugin_knowledge_category WHERE name='病害' AND parent_id=0 LIMIT 1) c
WHERE NOT EXISTS (
    SELECT 1 FROM hy_plugin_knowledge_category k WHERE k.name='细菌性病害' AND k.parent_id=c.id
);
INSERT INTO hy_plugin_knowledge_category (name, parent_id, sort_order)
SELECT '病毒性病害', c.id, 3
FROM (SELECT id FROM hy_plugin_knowledge_category WHERE name='病害' AND parent_id=0 LIMIT 1) c
WHERE NOT EXISTS (
    SELECT 1 FROM hy_plugin_knowledge_category k WHERE k.name='病毒性病害' AND k.parent_id=c.id
)
