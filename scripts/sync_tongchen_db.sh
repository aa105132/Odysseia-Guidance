#!/bin/bash
# 定期同步 tongchen 数据库从远程服务器到本地
# 注意：只同步 tongchen 数据库，不影响其他数据库（如 yueyue）

# 配置
REMOTE_HOST="54.67.121.61"
REMOTE_USER="tongchen"
REMOTE_DB="tongchen"
REMOTE_PASSWORD="a123456"

LOCAL_CONTAINER="yueyue-postgres"
LOCAL_USER="tongchen"
LOCAL_DB="tongchen"

LOG_FILE="/var/log/tongchen_sync.log"
BACKUP_FILE="/tmp/tongchen_backup.sql"

# 记录开始时间
echo "$(date '+%Y-%m-%d %H:%M:%S') - 开始同步 tongchen 数据库..." >> $LOG_FILE

# 方法：使用 --clean 选项，先删除对象再创建，不需要删除整个数据库
# 这样可以安全地更新数据而不影响其他数据库
docker exec -e PGPASSWORD=$REMOTE_PASSWORD $LOCAL_CONTAINER \
    pg_dump -h $REMOTE_HOST -U $REMOTE_USER -d $REMOTE_DB \
    --clean --if-exists --no-owner --no-privileges \
    2>> $LOG_FILE | \
    docker exec -i $LOCAL_CONTAINER psql -U $LOCAL_USER -d $LOCAL_DB 2>> $LOG_FILE

# 检查结果
if [ $? -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - tongchen 数据库同步成功！" >> $LOG_FILE
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - tongchen 数据库同步失败！" >> $LOG_FILE
fi