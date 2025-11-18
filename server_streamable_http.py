#!/usr/bin/env python3
"""
Streamable-HTTP MCP Server + Douyin Tools
"""

import re
import json
import os
import asyncio
import requests
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from sys import stdout
from fastmcp import FastMCP
from loguru import logger
from playwright.async_api import Playwright, async_playwright, Page

BASE_DIR = Path(__file__).parent.resolve()
LOCAL_CHROME_PATH = "C:\Program Files\Google\Chrome\Application\chrome.exe"   # change me necessary！ for example C:/Program Files/Google/Chrome/Application/chrome.exe

# -------------------- MCP 初始化 --------------------
mcp = FastMCP("streamable-http-server")

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) '
        'AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 '
        'Version/17.0 Mobile/15E148 Safari/604.1'
    )
}

# -------------------- 工具函数 --------------------
def get_absolute_path(relative_path: str, base_dir: str = None) -> str:
    """将相对路径转换为绝对路径"""
    absolute_path = Path(BASE_DIR) / base_dir / relative_path
    return str(absolute_path)


def get_title_and_hashtags(filename: str):
    """获取视频标题和 hashtag"""
    txt_filename = filename.replace(".mp4", ".txt")
    with open(txt_filename, "r", encoding="utf-8") as f:
        content = f.read()
    lines = content.strip().split("\n")
    title = lines[0]
    hashtags = lines[1].replace("#", "").split(" ") if len(lines) > 1 else []
    return title, hashtags


def generate_schedule_time_next_day(total_videos, videos_per_day=1, daily_times=None, timestamps=False, start_days=0):
    """生成视频上传排程，从下一天开始"""
    if videos_per_day <= 0:
        raise ValueError("videos_per_day should be a positive integer")

    if daily_times is None:
        daily_times = [6, 11, 14, 16, 22]

    if videos_per_day > len(daily_times):
        raise ValueError("videos_per_day should not exceed the length of daily_times")

    schedule = []
    current_time = datetime.now()

    for video in range(total_videos):
        day = video // videos_per_day + start_days + 1
        daily_video_index = video % videos_per_day
        hour = daily_times[daily_video_index]
        offset = timedelta(days=day, hours=hour - current_time.hour,
                           minutes=-current_time.minute,
                           seconds=-current_time.second,
                           microseconds=-current_time.microsecond)
        schedule.append(current_time + offset)

    if timestamps:
        schedule = [int(t.timestamp()) for t in schedule]
    return schedule

# -------------------- Logger --------------------
def log_formatter(record: dict) -> str:
    colors = {
        "TRACE": "#cfe2f3",
        "INFO": "#9cbfdd",
        "DEBUG": "#8598ea",
        "WARNING": "#dcad5a",
        "SUCCESS": "#3dd08d",
        "ERROR": "#ae2c2c"
    }
    color = colors.get(record["level"].name, "#b3cfe7")
    return f"<fg #70acde>{{time:YYYY-MM-DD HH:mm:ss}}</fg #70acde> | <fg {color}>{{level}}</fg {color}>: <light-white>{{message}}</light-white>\n"


def create_logger(log_name: str, file_path: str):
    def filter_record(record):
        return record["extra"].get("business_name") == log_name

    Path(BASE_DIR / file_path).parent.mkdir(exist_ok=True)
    logger.add(
        Path(BASE_DIR / file_path),
        filter=filter_record,
        level="INFO",
        rotation="10 MB",
        retention="10 days",
        backtrace=True,
        diagnose=True
    )
    return logger.bind(business_name=log_name)


# Remove default handlers
logger.remove()
logger.add(stdout, colorize=True, format=log_formatter)

douyin_logger = create_logger('douyin', 'logs/douyin.log')

# -------------------- Douyin Cookie 验证 --------------------
async def set_init_script(context):
    stealth_js_path = Path(BASE_DIR / "stealth.min.js")
    await context.add_init_script(path=stealth_js_path)
    return context


async def cookie_auth(account_file):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=account_file)
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto("https://creator.douyin.com/creator-micro/content/upload")
        try:
            await page.wait_for_url("https://creator.douyin.com/creator-micro/content/upload", timeout=5000)
        except:
            print("[+] cookie 失效")
            await context.close()
            await browser.close()
            return False
        if await page.get_by_text('手机号登录').count() or await page.get_by_text('扫码登录').count():
            print("[+] cookie 失效")
            return False
        print("[+] cookie 有效")
        return True


async def douyin_setup(account_file, handle=False):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            return False
        douyin_logger.info('[+] cookie 文件不存在或已失效，请扫码登录生成 cookie')
        await douyin_cookie_gen(account_file)
    return True


async def douyin_cookie_gen(account_file):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto("https://creator.douyin.com/")
        await page.pause()
        await context.storage_state(path=account_file)


# -------------------- DouYinVideo 类 --------------------
class DouYinVideo:
    def __init__(self, title, file_path, tags, publish_date: datetime, account_file,
                 thumbnail_path=None, productLink='', productTitle=''):
        self.title = title
        self.file_path = file_path
        self.tags = tags
        self.publish_date = publish_date
        self.account_file = account_file
        self.local_executable_path = LOCAL_CHROME_PATH
        self.thumbnail_path = thumbnail_path
        self.productLink = productLink
        self.productTitle = productTitle

    async def main(self):
        """入口方法"""
        async with async_playwright() as playwright:
            await self.upload(playwright)

    async def upload(self, playwright: Playwright) -> None:
        """上传视频流程"""
        # 启动浏览器
        if self.local_executable_path:
            browser = await playwright.chromium.launch(headless=False, executable_path=self.local_executable_path)
        else:
            browser = await playwright.chromium.launch(headless=False)

        # 创建上下文并加载 cookie
        context = await browser.new_context(storage_state=f"{self.account_file}")
        context = await set_init_script(context)
        page = await context.new_page()
        await page.goto("https://creator.douyin.com/creator-micro/content/upload")

        douyin_logger.info(f'[+] 正在上传: {self.title}.mp4')
        await page.wait_for_url("https://creator.douyin.com/creator-micro/content/upload")

        # 上传视频文件
        await page.locator("div[class^='container'] input").set_input_files(self.file_path)

        # 等待发布页面加载
        while True:
            try:
                await page.wait_for_url(
                    "https://creator.douyin.com/creator-micro/content/publish?enter_from=publish_page",
                    timeout=3000)
                douyin_logger.info("[+] 成功进入version_1发布页面")
                break
            except:
                try:
                    await page.wait_for_url(
                        "https://creator.douyin.com/creator-micro/content/post/video?enter_from=publish_page",
                        timeout=3000)
                    douyin_logger.info("[+] 成功进入version_2发布页面")
                    break
                except:
                    douyin_logger.info("[-] 未进入发布页面，重试...")
                    await asyncio.sleep(0.5)

        # 填充标题
        await asyncio.sleep(1)
        title_container = page.get_by_text('作品标题').locator("..").locator("xpath=following-sibling::div[1]").locator("input")
        if await title_container.count():
            await title_container.fill(self.title[:30])
        else:
            titlecontainer = page.locator(".notranslate")
            await titlecontainer.click()
            await page.keyboard.press("Control+KeyA")
            await page.keyboard.press("Delete")
            await page.keyboard.type(self.title)
            await page.keyboard.press("Enter")

        # 填充标签
        css_selector = ".zone-container"
        for tag in self.tags:
            await page.type(css_selector, "#" + tag)
            await page.press(css_selector, "Space")
        douyin_logger.info(f'总共添加 {len(self.tags)} 个话题')

        # 等待视频上传完成
        while True:
            try:
                number = await page.locator('[class^="long-card"] div:has-text("重新上传")').count()
                if number > 0:
                    douyin_logger.success("[-] 视频上传完毕")
                    break
                else:
                    douyin_logger.info("[-] 正在上传视频中...")
                    await asyncio.sleep(2)
                    if await page.locator('div.progress-div > div:has-text("上传失败")').count():
                        douyin_logger.error("[-] 上传失败，重试中")
                        await self.handle_upload_error(page)
            except:
                await asyncio.sleep(2)

        # 设置商品链接
        if self.productLink and self.productTitle:
            douyin_logger.info("[-] 正在设置商品链接...")
            await self.set_product_link(page, self.productLink, self.productTitle)
            douyin_logger.info("[+] 完成设置商品链接")

        # 上传封面
        await self.set_thumbnail(page, self.thumbnail_path)

        # 设置位置（如果有）
        await self.set_location(page, "")

        # 第三方平台开关
        third_part_element = '[class^="info"] > [class^="first-part"] div div.semi-switch'
        if await page.locator(third_part_element).count():
            if 'semi-switch-checked' not in await page.eval_on_selector(third_part_element, 'div => div.className'):
                await page.locator(third_part_element).locator('input.semi-switch-native-control').click()

        # 设置定时发布
        if self.publish_date != 0:
            await self.set_schedule_time_douyin(page, self.publish_date)

        # 发布视频
        while True:
            try:
                publish_button = page.get_by_role('button', name="发布", exact=True)
                if await publish_button.count():
                    await publish_button.click()
                await page.wait_for_url("https://creator.douyin.com/creator-micro/content/manage**", timeout=3000)
                douyin_logger.success("[-] 视频发布成功")
                break
            except:
                douyin_logger.info("[-] 视频正在发布中...")
                await asyncio.sleep(0.5)

        # 更新 cookie
        await context.storage_state(path=self.account_file)
        douyin_logger.success("[-] cookie 更新完毕！")
        await context.close()
        await browser.close()

    # -------------------- 其他辅助方法 --------------------
    async def handle_upload_error(self, page):
        douyin_logger.info('[-] 视频上传出错，重新上传中')
        await page.locator('div.progress-div [class^="upload-btn-input"]').set_input_files(self.file_path)

    async def set_thumbnail(self, page: Page, thumbnail_path: str):
        if not thumbnail_path:
            return
        douyin_logger.info('[-] 正在设置封面...')
        await page.click('text="选择封面"')
        await page.wait_for_selector("div.dy-creator-content-modal")
        await page.click('text="设置竖封面"')
        await page.wait_for_timeout(2000)
        await page.locator("div[class^='semi-upload upload'] >> input.semi-upload-hidden-input").set_input_files(thumbnail_path)
        await page.wait_for_timeout(2000)
        await page.locator("div#tooltip-container button:visible:has-text('完成')").click()
        douyin_logger.info('[+] 视频封面设置完成！')
        await page.wait_for_selector("div.extractFooter", state='detached')

    async def set_location(self, page: Page, location: str = ""):
        if not location:
            return
        await page.locator('div.semi-select span:has-text("输入地理位置")').click()
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(2000)
        await page.keyboard.type(location)
        await page.wait_for_selector('div[role="listbox"] [role="option"]', timeout=5000)
        await page.locator('div[role="listbox"] [role="option"]').first.click()

    async def handle_product_dialog(self, page: Page, product_title: str):
        await page.wait_for_timeout(2000)
        short_title_input = page.locator('input[placeholder="请输入商品短标题"]')
        if not await short_title_input.count():
            douyin_logger.error("[-] 未找到商品短标题输入框")
            return False
        await short_title_input.fill(product_title[:10])
        await page.wait_for_timeout(1000)
        finish_button = page.locator('button:has-text("完成编辑")')
        if 'disabled' not in await finish_button.get_attribute('class'):
            await finish_button.click()
            await page.wait_for_selector('.semi-modal-content', state='hidden', timeout=5000)
            return True
        else:
            cancel_button = page.locator('button:has-text("取消")')
            if await cancel_button.count():
                await cancel_button.click()
            else:
                close_button = page.locator('.semi-modal-close')
                await close_button.click()
            await page.wait_for_selector('.semi-modal-content', state='hidden', timeout=5000)
            return False

    async def set_product_link(self, page: Page, product_link: str, product_title: str):
        try:
            await page.wait_for_selector('text=添加标签', timeout=10000)
            dropdown = page.get_by_text('添加标签').locator("..").locator("..").locator("..").locator(".semi-select").first
            if not await dropdown.count():
                douyin_logger.error("[-] 未找到标签下拉框")
                return False
            await dropdown.click()
            await page.wait_for_selector('[role="listbox"]', timeout=5000)
            await page.locator('[role="option"]:has-text("购物车")').click()
            await page.wait_for_selector('input[placeholder="粘贴商品链接"]', timeout=5000)
            input_field = page.locator('input[placeholder="粘贴商品链接"]')
            await input_field.fill(product_link)
            add_button = page.locator('span:has-text("添加链接")')
            if 'disable' in await add_button.get_attribute('class'):
                douyin_logger.error("[-] 添加链接按钮不可用")
                return False
            await add_button.click()
            await page.wait_for_timeout(2000)
            error_modal = page.locator('text=未搜索到对应商品')
            if await error_modal.count():
                await page.locator('button:has-text("确定")').click()
                douyin_logger.error("[-] 商品链接无效")
                return False
            if not await self.handle_product_dialog(page, product_title):
                return False
            douyin_logger.debug("[+] 成功设置商品链接")
            return True
        except Exception as e:
            douyin_logger.error(f"[-] 设置商品链接出错: {str(e)}")
            return False

    async def set_schedule_time_douyin(self, page, publish_date):
        label_element = page.locator("[class^='radio']:has-text('定时发布')")
        await label_element.click()
        await asyncio.sleep(1)
        publish_date_hour = publish_date.strftime("%Y-%m-%d %H:%M")
        await asyncio.sleep(1)
        await page.locator('.semi-input[placeholder="日期和时间"]').click()
        await page.keyboard.press("Control+KeyA")
        await page.keyboard.type(str(publish_date_hour))
        await page.keyboard.press("Enter")
        await asyncio.sleep(1)



# -------------------- Douyin 解析工具 --------------------
class DouyinProcessor:
    def __init__(self):
        self.temp_dir = Path(tempfile.mkdtemp())

    def __del__(self):
        import shutil
        if hasattr(self, 'temp_dir') and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def parse_share_url(self, share_text: str) -> dict:
        urls = re.findall(r'http[s]?://[^\s]+', share_text)
        if not urls:
            raise ValueError("未找到有效的分享链接")

        share_url = urls[0]
        resp = requests.get(share_url, headers=HEADERS)
        video_id = resp.url.split("?")[0].strip("/").split("/")[-1]
        share_url = f'https://www.iesdouyin.com/share/video/{video_id}'
        response = requests.get(share_url, headers=HEADERS)
        response.raise_for_status()

        pattern = re.compile(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", flags=re.DOTALL)
        find_res = pattern.search(response.text)
        if not find_res or not find_res.group(1):
            raise ValueError("解析视频信息失败")

        json_data = json.loads(find_res.group(1).strip())
        VIDEO_ID_PAGE_KEY = "video_(id)/page"
        NOTE_ID_PAGE_KEY = "note_(id)/page"

        if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
            data = json_data["loaderData"][VIDEO_ID_PAGE_KEY]["videoInfoRes"]["item_list"][0]
        elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
            data = json_data["loaderData"][NOTE_ID_PAGE_KEY]["videoInfoRes"]["item_list"][0]
        else:
            raise Exception("无法解析视频信息")

        video_url = data["video"]["play_addr"]["url_list"][0].replace("playwm", "play")
        desc = data.get("desc", "").strip() or f"douyin_{video_id}"
        desc = re.sub(r'[\\/:*?"<>|]', '_', desc)

        return {"url": video_url, "title": desc, "video_id": video_id}


# -------------------- MCP 工具 --------------------
@mcp.tool()
def get_douyin_download_link(share_link: str) -> str:
    """解析抖音分享链接，返回无水印下载地址"""
    try:
        processor = DouyinProcessor()
        video_info = processor.parse_share_url(share_link)
        return json.dumps({
            "status": "success",
            "video_id": video_info["video_id"],
            "title": video_info["title"],
            "download_url": video_info["url"]
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False, indent=2)


@mcp.tool()
async def check_douyin_cookie() -> str:
    """检查账号 cookie 是否可用"""
    try:
        account_file = Path(BASE_DIR) / "account.json"
        cookie_setup = await douyin_setup(account_file, handle=True)
        if cookie_setup:
            return json.dumps({"status": "success", "message": "Cookie 有效"}, ensure_ascii=False)
        else:
            return json.dumps({"status": "error", "message": "Cookie 无效"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@mcp.tool()
async def upload_douyin_video(filepath: str, title: str = "", hashtags: str = "") -> str:
    """
    上传指定路径下的视频
    title: 视频标题
    hashtags: 用空格分隔的话题字符串，例如 "#爱情 #执着"
    """
    try:
        folder_path = Path(filepath)
        if not folder_path.exists() or not folder_path.is_dir():
            return json.dumps({"status": "error", "message": "文件夹不存在"}, ensure_ascii=False)
        
        # 获取文件夹中所有 MP4 文件
        files = list(folder_path.glob("*.mp4"))
        if not files:
            return json.dumps({"status": "error", "message": "文件夹中没有 MP4 文件"}, ensure_ascii=False)
        
        account_file = Path(BASE_DIR) / "account.json"
        publish_datetimes = generate_schedule_time_next_day(len(files), 1, daily_times=[16])
        cookie_setup = await douyin_setup(account_file, handle=False)
        if not cookie_setup:
            return json.dumps({"status": "error", "message": "Cookie 无效或登录失败"}, ensure_ascii=False)

        # hashtags 字符串处理成列表
        tag_list = [tag.strip().replace("#","") for tag in hashtags.split() if tag.strip()]

        uploaded_files = []
        for index, file in enumerate(files):
            # 使用输入参数的标题和 hashtags
            video_title = title if title else file.stem  # 如果没输入 title，则用文件名
            video_tags = tag_list

            app = DouYinVideo(video_title, file, video_tags, publish_datetimes[index], account_file)
            await app.main()

            uploaded_files.append({
                "file": str(file),
                "title": video_title,
                "tags": video_tags,
                "status": "uploaded"
            })

        return json.dumps({
            "status": "success",
            "uploaded": uploaded_files
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e)
        }, ensure_ascii=False, indent=2)
# -------------------- 启动 MCP 服务 --------------------
if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=18061)
