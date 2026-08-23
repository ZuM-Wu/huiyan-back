"""
迁移脚本 - 创建通知模块相关数据表
运行方式：在 HuiYan_Back 目录下执行
    python migrations/notice_module.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from core.db.base import engine


async def migrate():
    """创建通知模块数据表（幂等）"""
    async with engine.begin() as conn:
        # 1. 通知动作表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_notice_action'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_notice_action (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '动作 ID',
                    action_key VARCHAR(64) NOT NULL UNIQUE COMMENT '动作标识:user_registered',
                    action_name VARCHAR(128) NOT NULL COMMENT '动作名称：用户注册',
                    action_type VARCHAR(32) DEFAULT 'other' COMMENT '类型:user/order/production',
                    
                    -- 国内短信配置
                    sms_enabled TINYINT DEFAULT 0 COMMENT '是否启用短信:0 否 1 是',
                    sms_interface VARCHAR(64) DEFAULT '' COMMENT '短信接口标识：aliyun/idcsmart',
                    sms_template_id INT DEFAULT 0 COMMENT '短信模板 ID',
                    
                    -- 国际短信配置
                    sms_global_enabled TINYINT DEFAULT 0 COMMENT '是否启用国际短信',
                    sms_global_interface VARCHAR(64) DEFAULT '' COMMENT '国际短信接口标识',
                    sms_global_template_id INT DEFAULT 0 COMMENT '国际短信模板 ID',
                    
                    -- 邮件配置
                    email_enabled TINYINT DEFAULT 0 COMMENT '是否启用邮件',
                    email_interface VARCHAR(64) DEFAULT '' COMMENT '邮件接口标识：smtp/sendcloud',
                    email_template_id INT DEFAULT 0 COMMENT '邮件模板 ID',
                    
                    -- 联动配置
                    trigger_inbox TINYINT DEFAULT 0 COMMENT '发送时是否联动站内信',
                    
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通知动作配置表'
            """))
            print("[迁移] hy_notice_action 表已创建")
        else:
            print("[迁移] hy_notice_action 表已存在，跳过")

        # 2. 短信模板表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_sms_template'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_sms_template (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '模板 ID',
                    interface VARCHAR(64) NOT NULL COMMENT '接口标识:aliyun/idcsmart',
                    type TINYINT DEFAULT 0 COMMENT '类型:0=国内 1=国际',
                    template_id VARCHAR(128) DEFAULT '' COMMENT '第三方模板 ID',
                    title VARCHAR(128) NOT NULL COMMENT '模板标题：验证码',
                    content TEXT NOT NULL COMMENT '模板内容：验证码@var(code),5 分钟内有效',
                    signature VARCHAR(64) DEFAULT '' COMMENT '签名：【慧眼护农】',
                    status TINYINT DEFAULT 0 COMMENT '状态:0=草稿 1=待审核 2=已通过 3=未通过',
                    third_status TEXT COMMENT '第三方审核状态 JSON',
                    action_key VARCHAR(64) DEFAULT '' COMMENT '默认关联动作',
                    remark VARCHAR(256) DEFAULT '' COMMENT '备注',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                    UNIQUE KEY uk_interface_template (interface, template_id),
                    INDEX idx_action (action_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='短信模板表'
            """))
            print("[迁移] hy_sms_template 表已创建")
        else:
            print("[迁移] hy_sms_template 表已存在，跳过")

        # 3. 邮件模板表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_email_template'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_email_template (
                    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '模板 ID',
                    interface VARCHAR(64) NOT NULL COMMENT '接口标识：smtp/sendcloud',
                    name VARCHAR(128) NOT NULL COMMENT '模板名称：验证码',
                    subject VARCHAR(256) NOT NULL COMMENT '邮件主题',
                    content LONGTEXT NOT NULL COMMENT '邮件内容 (HTML)',
                    attachment VARCHAR(512) DEFAULT '' COMMENT '附件 URL 列表 JSON',
                    action_key VARCHAR(64) DEFAULT '' COMMENT '默认关联动作',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                    UNIQUE KEY uk_interface_name (interface, name),
                    INDEX idx_action (action_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='邮件模板表'
            """))
            print("[迁移] hy_email_template 表已创建")
        else:
            print("[迁移] hy_email_template 表已存在，跳过")

        # 4. 通知日志表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_notice_log'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_notice_log (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '日志 ID',
                    action_key VARCHAR(64) NOT NULL COMMENT '动作标识',
                    recipient VARCHAR(128) NOT NULL COMMENT '接收者 (手机/邮箱)',
                    channel VARCHAR(32) NOT NULL COMMENT '渠道:sms/email',
                    template_id INT NOT NULL COMMENT '模板 ID',
                    content TEXT COMMENT '发送内容摘要',
                    status TINYINT NOT NULL DEFAULT 0 COMMENT '状态:0=失败 1=成功',
                    error_msg TEXT COMMENT '错误信息',
                    recipient_id INT DEFAULT 0 COMMENT '接收者 ID(farmer_id/admin_id)',
                    extra JSON COMMENT '扩展字段 (message_id 等)',
                    send_time DATETIME COMMENT '发送时间',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    INDEX idx_action (action_key),
                    INDEX idx_recipient (recipient),
                    INDEX idx_send_time (send_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通知发送日志表'
            """))
            print("[迁移] hy_notice_log 表已创建")
        else:
            print("[迁移] hy_notice_log 表已存在，跳过")

        # 5. 站内信消息表
        result = await conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='hy_inbox_message'"
        ))
        if not result.scalar():
            await conn.execute(text("""
                CREATE TABLE hy_inbox_message (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '消息 ID',
                    sender_id INT DEFAULT 0 COMMENT '发件人 ID(系统=0)',
                    receiver_type VARCHAR(32) NOT NULL COMMENT '接收者类型:farmer/admin',
                    receiver_id INT NOT NULL COMMENT '接收者 ID',
                    title VARCHAR(256) NOT NULL COMMENT '消息标题',
                    content LONGTEXT COMMENT '消息内容',
                    is_read TINYINT DEFAULT 0 COMMENT '是否已读:0 未读 1 已读',
                    read_time DATETIME COMMENT '阅读时间',
                    priority TINYINT DEFAULT 0 COMMENT '优先级:0=普通 1=重要',
                    extra JSON COMMENT '扩展字段 (log_id 等)',
                    create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    INDEX idx_receiver (receiver_type, receiver_id),
                    INDEX idx_read (is_read)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='站内信消息表'
            """))
            print("[迁移] hy_inbox_message 表已创建")
        else:
            print("[迁移] hy_inbox_message 表已存在，跳过")

    print("\n[完成] 通知模块数据表迁移完毕")


if __name__ == "__main__":
    asyncio.run(migrate())
