CREATE TABLE IF NOT EXISTS hy_huiyan_iot_device (
    id BIGINT NOT NULL AUTO_INCREMENT COMMENT '慧眼平台设备主键',
    device_id VARCHAR(128) NOT NULL COMMENT '设备注册标识',
    device_name VARCHAR(128) NOT NULL COMMENT '设备名称',
    device_type VARCHAR(32) NOT NULL DEFAULT '' COMMENT '设备类型编码',
    device_type_label VARCHAR(64) NOT NULL DEFAULT '' COMMENT '设备类型名称',
    nickname VARCHAR(128) NOT NULL DEFAULT '' COMMENT '设备显示名称',
    address VARCHAR(256) NOT NULL DEFAULT '' COMMENT '设备安装地址',
    latitude VARCHAR(32) NOT NULL DEFAULT '' COMMENT '设备纬度',
    longitude VARCHAR(32) NOT NULL DEFAULT '' COMMENT '设备经度',
    capabilities JSON NOT NULL COMMENT '设备能力列表',
    metadata JSON NOT NULL COMMENT '设备扩展信息',
    last_seen DATETIME NULL COMMENT '最近心跳时间',
    create_time DATETIME NOT NULL COMMENT '注册时间',
    update_time DATETIME NOT NULL COMMENT '更新时间',
    PRIMARY KEY (id), UNIQUE KEY uk_huiyan_iot_device_id (device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='慧眼自有物联网平台设备表';
