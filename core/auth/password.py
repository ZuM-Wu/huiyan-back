# coding: utf-8
"""
密码工具模块
统一密码哈希与验证，支持 bcrypt 和旧版 SHA-256 渐进式迁移
"""

import hashlib
import logging

logger = logging.getLogger(__name__)

# 直接使用 bcrypt 包，避免 passlib 在 Windows 上的 72 字节限制 Bug
try:
    import bcrypt
    _use_bcrypt = True
except ImportError:
    _use_bcrypt = False
    logger.warning("bcrypt 库未安装，密码功能将不可用")


def hash_password(plain: str) -> str:
    """
    生成密码哈希（bcrypt）

    参数:
        plain: 明文密码
    返回:
        bcrypt 哈希字符串
    """
    if not _use_bcrypt:
        raise RuntimeError("bcrypt 库未安装")
    # bcrypt.gensalt() 生成默认 rounds=12 的 salt
    return bcrypt.hashpw(plain.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain: str, stored: str) -> bool:
    """
    验证密码 -- 兼容旧 SHA-256 格式

    检测逻辑:
        - 若 stored 为 64 位十六进制字符串（SHA-256），使用 hashlib 比对
        - 若 stored 为 bcrypt 格式（$2b$开头），使用 passlib 比对

    参数:
        plain:  用户输入的明文密码
        stored: 数据库中存储的哈希值
    返回:
        True 表示密码匹配
    """
    if not stored:
        return False

    # 判断是否为旧版 SHA-256 格式（64位十六进制，兼容大小写）
    if len(stored) == 64 and all(c in '0123456789abcdefABCDEF' for c in stored):
        sha_hash = hashlib.sha256(plain.encode()).hexdigest()
        return sha_hash.lower() == stored.lower()

    # bcrypt 格式，使用 native bcrypt 验证
    try:
        stored_bytes = stored.encode('utf-8')
        return bcrypt.checkpw(plain.encode('utf-8'), stored_bytes)
    except Exception as e:
        logger.warning("密码验证异常：%s", e)
        return False


def needs_rehash(stored: str) -> bool:
    """
    检查存储的哈希是否需要升级（旧格式 -> bcrypt）

    参数:
        stored: 数据库中存储的哈希值
    返回:
        True 表示需要重新哈希为 bcrypt
    """
    if not stored:
        return False
    # 64位十六进制 = 旧版 SHA-256，需要迁移（兼容大小写）
    if len(stored) == 64 and all(c in '0123456789abcdefABCDEF' for c in stored):
        return True
    # 检查 bcrypt 是否需要升级
    # 使用 passlib 检测，但忽略可能的异常
    try:
        from passlib.context import CryptContext
        pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        return pwd_ctx.needs_update(stored)
    except Exception:
        # passlib 可能在 windows 上报错，保守认为不需要更新
        return False
