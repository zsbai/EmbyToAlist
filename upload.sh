#!/bin/bash

# 检查是否有输入路径
if [ -z "$1" ]; then
    echo "Usage: $0 <path> [debug]"
    exit 1
fi

# 输入的路径

# Rclone 配置（使用默认配置）
RCLONE_PATH="/usr/bin/rclone"
RCLONE_OPTIONS="--transfers=10 -Pv --dry-run"
RCLONE_REMOTE=$1
shift

# 修改路径，删除或添加目录
PATH_PREFIX_REMOVE="/volume/data"
PATH_PREFIX_ADD=""
PATH_PREFIX_MOUNT_ADD=""

# 缓存路径
CACHE_PATH="/root/cache"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO] $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}[WARNING] $1${NC}"
}

log_error() {
    echo -e "${RED}[ERROR] $1${NC}"
}

log_success() {
    echo -e "${GREEN}[SUCCESS] $1${NC}"
}

commands=(
    "rclone"
    "mediainfo"
    "md5sum"
)

for cmd in "${commands[@]}"; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Command $cmd not found"
        exit 1
    fi
done

# 上传整个目录
upload_directory() {
    local src_path=$1
    local dest_path=$2
    local config_name=$3

    log_info "Uploading entire directory from $src_path to $config_name:$dest_path"
    $RCLONE_PATH copy "$src_path" "$config_name:$dest_path" $RCLONE_OPTIONS
    if [[ $? -ne 0 ]]; then
        log_error "Error: Failed to upload directory from $src_path to $config_name:$dest_path"
        exit 1
    fi
}

# 创建缓存
create_cache() {
    local path=$1
    local cache_path=$2
    local bitrate=$3
    local mount_path=$4

    # # 调整路径格式，用于计算 MD5
    # local md5_path="${file_path/volume\/data\/movie/movie}"
    # md5_path="${md5_path/volume\/data\/anime/anime}"

    # 计算前15秒的字节大小（位率单位为bps，转换为字节需要除以8）
    local cache_size=$(((bitrate * 15) / 8))

    # 计算路径字符串的 MD5 哈希
    local hash=$(echo -n "${MOUNT_PATH}" | md5sum | awk '{print $1}')
    local subdirname=${hash:0:2}

    # 创建缓存目录
    local cache_dir="$cache_path/$subdirname/$hash"
    mkdir -p "$cache_dir"

    # 使用 dd 命令从视频文件中提取前 15 秒数据作为缓存
    dd if="$path" of="${cache_dir}/cache_file_0_$((cache_size-1))" bs=1M count=$(($cache_size / 1024 / 1024))
    if [[ $? -ne 0 ]]; then
        log_error "Error: Failed to create cache for file: $path"
        exit 1
    fi

    log_success "Cache created at ${cache_dir}/cache_file_0_$((cache_size-1)) using path for MD5: $md5_path"
    log_success "Cache size: $cache_size bytes, Cache path: ${cache_dir}/cache_file_0_$((cache_size-1))"
}

# 处理输入的目录
process_directory() {
    local file_path=$1
    local dest_path=$2
    local mount_path=$3

    log_info "Processing directory: $file_path"
    log_info "Destination on cloud: $RCLONE_REMOTE:$dest_path"

    # 上传整个目录
    upload_directory "$file_path" "$dest_path" "$RCLONE_REMOTE"

    local bitrate=""
    
    # 搜索指定目录下的所有视频文件
    mapfile -t video_files < <(find "$file_path" -type f \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.avi" \))

    # 检查是否找到视频文件
    if [[ ${#video_files[@]} -eq 0 ]]; then
        log_error "Error: No video files found in the specified path."
        exit 1
    fi

    # 选择要处理的视频文件
    if [[ ${#video_files[@]} -gt 1 ]]; then
        # 查找第一集，命名为SxxE01
        for file in "${video_files[@]}"; do
            if [[ "$file" =~ S[0-9]{2}E01 ]]; then
                selected_file="$file"
                break
            fi
        done
        # 如果没有找到符合条件的文件
        if [[ -z "$selected_file" ]]; then
            log_error "Error: No first episode found in the video files."
            exit 1
        fi
    else
        # 只有一个视频文件时，直接使用该文件
        selected_file="${video_files[0]}"
    fi

    # 通过mediainfo获取bitrate
    bitrate=$(mediainfo --Output="General;%OverallBitRate%" "$selected_file")
    if [[ $? -ne 0 ]] || [[ -z "$bitrate" ]]; then
        log_error "Error: Failed to retrieve bitrate for file: $selected_file"
        exit 1
    fi

    # 如果文件数量大于1, 则循环创建缓存
    if [[ ${#video_files[@]} -gt 1 ]]; then
        for file in "${video_files[@]}"; do
            log_info "Processing file: $file, bitrate: $bitrate bps"
            create_cache "$file" "$CACHE_PATH" "$bitrate" "$mount_path"
            log_success "Processing complete for file: $file, bitrate: $bitrate bps"
        done
    else
        log_info "Processing file: $selected_file, bitrate: $bitrate bps"
        create_cache "$selected_file" "$CACHE_PATH" "$bitrate" "$mount_path"
        log_success "Processing complete for file: $selected_file, bitrate: $bitrate bps"
    fi

}

# 主程序
for INPUT_PATH in "$@"; do
    if [[ -d "$INPUT_PATH" ]]; then

        DEST_PATH="${INPUT_PATH#$PATH_PREFIX_REMOVE}"
        DEST_PATH="${PATH_PREFIX_ADD}/${DEST_PATH#/}"
        MOUNT_PATH="${DEST_PATH%/${DEST_PATH#/}}"

        log_info "Input path: ${INPUT_PATH}"
        log_info "Destination path: ${DEST_PATH}"
        log_info "Rclone remote: ${RCLONE_REMOTE}"
        log_info "Ctrl+C to cancel, or wait 3 seconds to continue..."
        sleep 3

        process_directory "$INPUT_PATH" "$DEST_PATH" "$MOUNT_PATH"
    else
        log_error "Error: Input path \"$INPUT_PATH\" is not a directory."
        exit 1
    fi
done