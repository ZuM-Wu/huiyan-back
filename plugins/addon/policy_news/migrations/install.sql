CREATE TABLE IF NOT EXISTS hy_plugin_policy_news_item (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '条目主键',
    source VARCHAR(32) NOT NULL COMMENT '政策来源',
    title VARCHAR(512) NOT NULL COMMENT '政策标题',
    url VARCHAR(700) NOT NULL COMMENT '政策详情地址',
    published_at DATETIME NULL COMMENT '发布日期',
    first_seen_at DATETIME NOT NULL COMMENT '首次发现时间',
    last_seen_at DATETIME NOT NULL COMMENT '最后抓取时间',
    summary TEXT NULL COMMENT '政策摘要',
    UNIQUE KEY uq_policy_news_source_url (source, url),
    KEY ix_policy_news_source (source),
    KEY ix_policy_news_url (url),
    KEY ix_policy_news_published_at (published_at),
    KEY ix_policy_news_last_seen_at (last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='农业政策资讯条目';

CREATE TABLE IF NOT EXISTS hy_plugin_policy_news_state (
    id INT PRIMARY KEY AUTO_INCREMENT COMMENT '状态主键',
    source VARCHAR(32) NOT NULL COMMENT '政策来源',
    last_attempt_at DATETIME NULL COMMENT '最近尝试时间',
    last_success_at DATETIME NULL COMMENT '最近成功时间',
    last_error VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '最近错误信息',
    item_count INT NOT NULL DEFAULT 0 COMMENT '来源条目数量',
    UNIQUE KEY uq_policy_news_state_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='农业政策资讯来源抓取状态';

INSERT IGNORE INTO hy_plugin_policy_news_state (source) VALUES
    ('农业农村部'), ('广西农业农村厅');
