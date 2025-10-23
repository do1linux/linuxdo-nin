"""
cron: 0 */6 * * *
new Env("Linux.Do 签到")
"""

import os
import random
import time
import functools
import sys
import json
import hashlib
from datetime import datetime
from loguru import logger
from DrissionPage import ChromiumOptions, Chromium
from tabulate import tabulate


def retry_decorator(retries=3):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == retries - 1:  # 最后一次尝试
                        logger.error(f"函数 {func.__name__} 最终执行失败: {str(e)}")
                    logger.warning(
                        f"函数 {func.__name__} 第 {attempt + 1}/{retries} 次尝试失败: {str(e)}"
                    )
                    time.sleep(1)
            return None

        return wrapper

    return decorator


os.environ.pop("DISPLAY", None)
os.environ.pop("DYLD_LIBRARY_PATH", None)

USERNAME = os.getenv("LINUXDO_USERNAME")
PASSWORD = os.getenv("LINUXDO_PASSWORD")
BROWSE_ENABLED = os.getenv("BROWSE_ENABLED", "true").strip().lower() not in [
    "false",
    "0",
    "off",
]
if not USERNAME:
    USERNAME = os.getenv("USERNAME")
if not PASSWORD:
    PASSWORD = os.getenv("PASSWORD")

HOME_URL = "https://linux.do/"
LOGIN_URL = "https://linux.do/login"


# ======================== 缓存管理器 ========================
class CacheManager:
    """缓存管理类，负责缓存文件的读写和管理"""
    
    @staticmethod
    def get_file_age_hours(file_path):
        """获取文件年龄（小时）"""
        if not os.path.exists(file_path):
            return None
        file_mtime = os.path.getmtime(file_path)
        current_time = time.time()
        age_hours = (current_time - file_mtime) / 3600
        return age_hours

    @staticmethod
    def load_cache(file_name):
        """从文件加载缓存数据"""
        if os.path.exists(file_name):
            try:
                with open(file_name, "r", encoding='utf-8') as f:
                    data = json.load(f)
                
                age_hours = CacheManager.get_file_age_hours(file_name)
                if age_hours is not None:
                    age_status = "全新" if age_hours < 0.1 else "较新" if age_hours < 6 else "较旧"
                    logger.info(f"📦 加载缓存 {file_name} (年龄: {age_hours:.3f}小时, {age_status})")
                
                return data.get('data', data)
            except Exception as e:
                logger.warning(f"缓存加载失败 {file_name}: {str(e)}")
        else:
            logger.info(f"📭 缓存文件不存在: {file_name}")
        return None

    @staticmethod
    def save_cache(data, file_name):
        """保存数据到缓存文件"""
        try:
            data_to_save = {
                'data': data,
                'cache_timestamp': datetime.now().isoformat(),
                'cache_version': '1.0',
                'file_created': time.time(),
            }
            
            with open(file_name, "w", encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
            
            current_time = time.time()
            os.utime(file_name, (current_time, current_time))
            
            new_age = CacheManager.get_file_age_hours(file_name)
            file_size = os.path.getsize(file_name)
            logger.info(f"💾 缓存已保存到 {file_name} (新年龄: {new_age:.3f}小时, 大小: {file_size} 字节)")
            return True
        except Exception as e:
            logger.error(f"缓存保存失败 {file_name}: {str(e)}")
            return False

    @staticmethod
    def load_cookies():
        """加载cookies缓存"""
        return CacheManager.load_cache("linuxdo_cookies.json")

    @staticmethod
    def save_cookies(cookies):
        """保存cookies到缓存"""
        return CacheManager.save_cache(cookies, "linuxdo_cookies.json")

    @staticmethod
    def load_session():
        """加载会话缓存"""
        return CacheManager.load_cache("linuxdo_session.json") or {}

    @staticmethod
    def save_session(session_data):
        """保存会话数据到缓存"""
        return CacheManager.save_cache(session_data, "linuxdo_session.json")


# ======================== Cloudflare处理器 ========================
class CloudflareHandler:
    """Cloudflare验证处理类"""
    
    @staticmethod
    def is_cf_cookie_valid(cookies):
        """检查Cloudflare cookie是否有效"""
        try:
            for cookie in cookies:
                if cookie.get('name') == 'cf_clearance':
                    expires = cookie.get('expires', 0)
                    if expires == -1 or expires > time.time():
                        return True
            return False
        except Exception:
            return False

    @staticmethod
    def handle_cloudflare(page, max_attempts=8, timeout=180):
        """
        处理Cloudflare验证
        
        Args:
            page: 页面对象
            max_attempts (int): 最大尝试次数
            timeout (int): 超时时间（秒）
            
        Returns:
            bool: 验证通过返回True，否则返回False
        """
        start_time = time.time()
        logger.info("🛡️ 开始处理 Cloudflare验证")
        
        # 检查缓存的Cloudflare cookies
        cached_cookies = CacheManager.load_cookies()
        cached_cf_valid = CloudflareHandler.is_cf_cookie_valid(cached_cookies or [])
        
        if cached_cf_valid:
            logger.success("✅ 检测到有效的缓存Cloudflare cookie")
            try:
                # 尝试使用缓存cookies访问
                if cached_cookies:
                    page.set.cookies(cached_cookies)
                    page.get(HOME_URL)
                    time.sleep(5)
                    
                    page_title = page.title
                    if page_title != "请稍候…" and "Checking" not in page_title:
                        logger.success("✅ 使用缓存成功绕过Cloudflare验证")
                        return True
            except Exception as e:
                logger.warning(f"使用缓存绕过失败: {str(e)}")
        
        # 完整验证流程
        logger.info("🔄 开始完整Cloudflare验证流程")
        for attempt in range(max_attempts):
            try:
                current_url = page.url
                page_title = page.title
                
                # 检查页面是否已经正常加载
                if page_title != "请稍候…" and "Checking" not in page_title:
                    logger.success("✅ 页面已正常加载，Cloudflare验证通过")
                    return True
                
                # 等待验证
                wait_time = random.uniform(8, 15)
                logger.info(f"⏳ 等待Cloudflare验证完成 ({wait_time:.1f}秒) - 尝试 {attempt + 1}/{max_attempts}")
                time.sleep(wait_time)
                
                # 检查超时
                if time.time() - start_time > timeout:
                    logger.warning("⚠️ Cloudflare处理超时")
                    break
                    
            except Exception as e:
                logger.error(f"Cloudflare处理异常 (尝试 {attempt + 1}): {str(e)}")
                time.sleep(10)
        
        # 最终检查
        page_title = page.title
        if page_title != "请稍候…" and "Checking" not in page_title:
            logger.success("✅ 最终验证: Cloudflare验证通过")
            return True
        else:
            logger.warning("⚠️ 最终验证: Cloudflare验证未完全通过，但继续后续流程")
            return True


class LinuxDoBrowser:
    def __init__(self) -> None:
        EXTENSION_PATH = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "turnstilePatch")
        )
        from sys import platform

        if platform == "linux" or platform == "linux2":
            platformIdentifier = "X11; Linux x86_64"
        elif platform == "darwin":
            platformIdentifier = "Macintosh; Intel Mac OS X 10_15_7"
        elif platform == "win32":
            platformIdentifier = "Windows NT 10.0; Win64; x64"

        co = (
            ChromiumOptions()
            .headless(True)
            .add_extension(EXTENSION_PATH)
            .incognito(True)
            .set_argument("--no-sandbox")
            .set_argument("--disable-blink-features=AutomationControlled")
            .set_argument("--disable-features=VizDisplayCompositor")
            .set_argument("--disable-background-timer-throttling")
            .set_argument("--disable-backgrounding-occluded-windows")
            .set_argument("--disable-renderer-backgrounding")
            .set_argument("--lang=zh-CN,zh;q=0.9,en;q=0.8")
        )
        co.set_user_agent(
            f"Mozilla/5.0 ({platformIdentifier}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        self.browser = Chromium(co)
        self.page = self.browser.new_tab()
        
        # 加载会话数据
        self.session_data = CacheManager.load_session()
        self.cache_saved = False
        
        # 注入增强的反检测脚本
        self.inject_enhanced_script()

    def inject_enhanced_script(self):
        """注入增强的反检测和统计触发脚本"""
        enhanced_script = """
        // 增强的反检测脚本
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        
        // 模拟完整的浏览器环境
        Object.defineProperty(navigator, 'plugins', { 
            get: () => [1, 2, 3, 4, 5],
            configurable: true
        });
        
        Object.defineProperty(navigator, 'languages', { 
            get: () => ['zh-CN', 'zh', 'en-US', 'en'] 
        });
        
        // 屏蔽自动化特征
        window.chrome = { 
            runtime: {},
            loadTimes: function() {},
            csi: function() {}, 
            app: {isInstalled: false}
        };
        
        // 页面可见性API
        Object.defineProperty(document, 'hidden', { get: () => false });
        Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
        
        // 统计请求拦截和确保
        const originalFetch = window.fetch;
        window.fetch = function(...args) {
            const url = args[0];
            if (typeof url === 'string' && 
                (url.includes('analytics') || url.includes('statistics') || 
                 url.includes('track') || url.includes('count'))) {
                console.log('📊 统计请求被发送:', url);
                return originalFetch.apply(this, args).catch(() => {
                    return Promise.resolve(new Response(null, {status: 200}));
                });
            }
            return originalFetch.apply(this, args);
        };
        
        // XMLHttpRequest拦截
        const originalXHROpen = XMLHttpRequest.prototype.open;
        const originalXHRSend = XMLHttpRequest.prototype.send;
        
        XMLHttpRequest.prototype.open = function(method, url, ...rest) {
            this._url = url;
            return originalXHROpen.apply(this, [method, url, ...rest]);
        };
        
        XMLHttpRequest.prototype.send = function(...args) {
            if (this._url && (this._url.includes('analytics') || 
                this._url.includes('statistics') || this._url.includes('count'))) {
                this.addEventListener('load', () => {
                    console.log('统计请求完成:', this._url);
                });
                this.addEventListener('error', () => {
                    console.log('统计请求失败，但继续执行:', this._url);
                });
            }
            return originalXHRSend.apply(this, args);
        };
        
        // 用户行为事件模拟
        document.addEventListener('DOMContentLoaded', () => {
            setTimeout(() => {
                window.dispatchEvent(new Event('pageview'));
                if (typeof window.onPageView === 'function') {
                    window.onPageView();
                }
            }, 1000);
        });
        
        // 滚动事件统计
        let lastScrollTime = 0;
        window.addEventListener('scroll', () => {
            const now = Date.now();
            if (now - lastScrollTime > 500) {
                lastScrollTime = now;
                window.dispatchEvent(new CustomEvent('scrollActivity', {
                    detail: { 
                        scrollY: window.scrollY,
                        scrollPercent: (window.scrollY / (document.body.scrollHeight - window.innerHeight)) * 100
                    }
                }));
            }
        });
        
        console.log('🔧 增强的JS环境模拟已加载');
        """
        
        try:
            self.page.run_js(enhanced_script)
            logger.info("✅ 增强的反检测脚本已注入")
        except Exception as e:
            logger.warning(f"注入反检测脚本失败: {str(e)}")

    def save_all_caches(self):
        """统一保存所有缓存"""
        try:
            # 保存cookies
            cookies = self.browser.cookies()
            if cookies:
                CacheManager.save_cookies(cookies)
            
            # 更新并保存会话数据
            self.session_data.update({
                'last_success': datetime.now().isoformat(),
                'login_status': 'success',
                'last_updated': datetime.now().isoformat(),
            })
            CacheManager.save_session(self.session_data)
            
            logger.info("✅ 所有缓存已保存")
            self.cache_saved = True
        except Exception as e:
            logger.error(f"保存缓存失败: {str(e)}")

    def clear_caches(self):
        """清除所有缓存文件"""
        try:
            cache_files = ["linuxdo_cookies.json", "linuxdo_session.json"]
            for file_name in cache_files:
                if os.path.exists(file_name):
                    os.remove(file_name)
                    logger.info(f"🗑️ 已清除缓存: {file_name}")
            
            self.session_data = {}
            logger.info("✅ 所有缓存已清除")
            
        except Exception as e:
            logger.error(f"清除缓存失败: {str(e)}")

    def try_cache_first_approach(self):
        """
        尝试缓存优先访问策略
        
        Returns:
            bool: 缓存访问成功返回True，否则返回False
        """
        try:
            # 检查是否有有效的Cloudflare缓存
            cached_cookies = CacheManager.load_cookies()
            cached_cf_valid = CloudflareHandler.is_cf_cookie_valid(cached_cookies or [])
            
            if cached_cf_valid:
                logger.info("✅ 检测到有效的Cloudflare缓存，尝试直接访问")
                # 设置缓存cookies
                if cached_cookies:
                    self.page.set.cookies(cached_cookies)
                
                self.page.get(HOME_URL)
                time.sleep(5)
                
                login_status = self.check_login_status()
                if login_status:
                    logger.success("✅ 缓存优先流程成功 - 已登录")
                    return True
                else:
                    logger.warning("⚠️ Cloudflare缓存有效但未登录，尝试登录")
                    return False
            else:
                logger.info("📭 无有效Cloudflare缓存")
                return False
                
        except Exception as e:
            logger.error(f"缓存优先流程异常: {str(e)}")
            return False

    def check_login_status(self):
        """检查登录状态"""
        try:
            # 检查用户相关元素
            user_indicators = [
                '#current-user', '#toggle-current-user', '.header-dropdown-toggle.current-user',
                'img.avatar', '.user-menu', '[data-user-menu]'
            ]
            
            for selector in user_indicators:
                try:
                    user_elem = self.page.ele(selector)
                    if user_elem:
                        logger.success(f"✅ 检测到用户元素: {selector}")
                        return self.verify_username()
                except Exception:
                    continue
            
            # 检查登录按钮
            login_buttons = [
                '.login-button', 'button:has-text("登录")', 
                'button:has-text("Log In")', '.btn.btn-icon-text.login-button'
            ]
            
            for selector in login_buttons:
                try:
                    login_btn = self.page.ele(selector)
                    if login_btn:
                        logger.warning(f"❌ 检测到登录按钮: {selector}")
                        return False
                except Exception:
                    continue
            
            # 如果无法确定状态
            page_content = self.page.html
            if "请稍候" not in self.page.title and "Checking" not in self.page.title:
                if USERNAME.lower() in page_content.lower():
                    logger.success(f"✅ 在页面内容中找到用户名: {USERNAME}")
                    return True
                
                if len(page_content) > 1000:
                    logger.success("✅ 页面显示正常内容，可能已登录")
                    return True
            
            logger.warning(f"⚠️ 登录状态不确定，默认认为未登录。页面标题: {self.page.title}")
            return False
            
        except Exception as e:
            logger.warning(f"检查登录状态时出错: {str(e)}")
            return False

    def verify_username(self):
        """验证用户名是否显示在页面上"""
        # 方法1: 页面内容检查
        page_content = self.page.html
        if USERNAME.lower() in page_content.lower():
            logger.success(f"✅ 在页面内容中找到用户名: {USERNAME}")
            return True
        
        # 方法2: 用户菜单点击
        try:
            user_click_selectors = ['img.avatar', '.current-user', '[data-user-menu]', '.header-dropdown-toggle']
            for selector in user_click_selectors:
                user_elem = self.page.ele(selector)
                if user_elem:
                    user_elem.click()
                    time.sleep(2)
                    
                    user_menu_content = self.page.html
                    if USERNAME.lower() in user_menu_content.lower():
                        logger.success(f"✅ 在用户菜单中找到用户名: {USERNAME}")
                        # 点击其他地方关闭菜单
                        self.page.ele('body').click()
                        return True
                    
                    self.page.ele('body').click()
                    time.sleep(1)
                    break
        except Exception:
            pass
        
        logger.warning(f"⚠️ 检测到用户元素但无法验证用户名 {USERNAME}，默认认为未登录")
        return False

    def getTurnstileToken(self):
        self.page.run_js("try { turnstile.reset() } catch(e) { }")

        turnstileResponse = None

        for i in range(0, 5):
            try:
                turnstileResponse = self.page.run_js(
                    "try { return turnstile.getResponse() } catch(e) { return null }"
                )
                if turnstileResponse:
                    return turnstileResponse

                challengeSolution = self.page.ele("@name=cf-turnstile-response")
                challengeWrapper = challengeSolution.parent()
                challengeIframe = challengeWrapper.shadow_root.ele("tag:iframe")
                challengeIframeBody = challengeIframe.ele("tag:body").shadow_root
                challengeButton = challengeIframeBody.ele("tag:input")
                challengeButton.click()
            except Exception as e:
                logger.warning(f"处理 Turnstile 时出错: {str(e)}")
            time.sleep(1)

    def login(self):
        """登录流程"""
        # 首先尝试缓存优先访问
        cache_success = self.try_cache_first_approach()
        if cache_success:
            logger.success("✅ 缓存登录成功")
            if not self.cache_saved:
                self.save_all_caches()
            return True

        logger.info("开始登录")
        self.page.get(LOGIN_URL)
        time.sleep(2)
        
        # 处理Cloudflare验证
        cf_success = CloudflareHandler.handle_cloudflare(self.page)
        if not cf_success:
            logger.warning("⚠️ Cloudflare验证可能未完全通过，但继续登录流程")
        
        turnstile_token = self.getTurnstileToken()
        logger.info(f"turnstile_token: {turnstile_token}")
        
        self.page.ele("@id=login-account-name").input(USERNAME)
        self.page.ele("@id=login-account-password").input(PASSWORD)
        self.page.ele("@id=login-button").click()
        time.sleep(10)
        
        login_success = self.check_login_status()
        if login_success:
            logger.info("登录成功")
            if not self.cache_saved:
                self.save_all_caches()
            return True
        else:
            logger.error("登录失败")
            return False

    def click_topic(self):
        topic_list = self.page.ele("@id=list-area").eles(".:title")
        if not topic_list:
            logger.warning("未找到主题帖，尝试刷新页面")
            self.page.refresh()
            time.sleep(3)
            topic_list = self.page.ele("@id=list-area").eles(".:title")
            
        topic_count = len(topic_list)
        browse_count = min(random.randint(4, 8), topic_count)
        logger.info(f"发现 {topic_count} 个主题帖，随机选择 {browse_count} 个进行深度浏览")
        
        selected_topics = random.sample(topic_list, browse_count)
        for i, topic in enumerate(selected_topics):
            logger.info(f"📖 浏览进度: {i+1}/{browse_count}")
            self.click_one_topic(topic.attr("href"))
            
            # 更新浏览历史
            browse_history = self.session_data.get('browse_history', [])
            browse_history.append(topic.attr("href"))
            self.session_data['browse_history'] = browse_history[-50:]  # 保留最近50条
            
            # 主题间随机延迟
            if i < browse_count - 1:
                delay = random.uniform(8, 15)
                logger.info(f"⏳ 主题间延迟 {delay:.1f} 秒")
                time.sleep(delay)

    @retry_decorator()
    def click_one_topic(self, topic_url):
        new_page = self.browser.new_tab()
        try:
            full_url = f"https://linux.do{topic_url}" if topic_url.startswith('/') else topic_url
            new_page.get(full_url)
            
            # 等待页面完全加载
            time.sleep(3)
            
            # 触发统计事件
            self.trigger_statistical_events(new_page)
            
            # 增强的浏览行为
            self.enhanced_browse_post(new_page)
            
            # 随机点赞
            if random.random() < 0.25:
                self.click_like(new_page)
                
        finally:
            new_page.close()

    def enhanced_browse_post(self, page):
        """增强的浏览行为，确保统计被正确计数"""
        try:
            # 获取页面内容信息
            content_info = page.run_js("""
                function getContentInfo() {
                    const content = document.querySelector('.topic-post .cooked') || 
                                   document.querySelector('.post-content') ||
                                   document.querySelector('.post-body') ||
                                   document.body;
                    return {
                        length: content.textContent.length,
                        height: content.scrollHeight,
                        wordCount: content.textContent.split(/\\s+/).length,
                        imageCount: content.querySelectorAll('img').length
                    };
                }
                return getContentInfo();
            """)
            
            # 基于内容计算阅读时间
            base_time = max(30, min(300, content_info['length'] / 40))
            read_time = base_time * random.uniform(0.8, 1.3)
            
            logger.info(f"📖 预计阅读时间: {read_time:.1f}秒 (长度:{content_info['length']}字符)")
            
            # 分段滚动模拟
            scroll_segments = random.randint(6, 12)
            time_per_segment = read_time / scroll_segments
            
            for segment in range(scroll_segments):
                # 计算滚动位置
                scroll_ratio = (segment + 1) / scroll_segments
                scroll_pos = content_info['height'] * scroll_ratio
                
                # 平滑滚动
                page.run_js(f"""
                    window.scrollTo({{
                        top: {scroll_pos},
                        behavior: 'smooth'
                    }});
                """)
                
                # 模拟交互
                if random.random() < 0.4:
                    self.simulate_user_interaction(page)
                
                # 分段停留
                segment_wait = time_per_segment * random.uniform(0.7, 1.2)
                time.sleep(segment_wait)
            
            # 最终滚动到底部
            page.run_js("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
            time.sleep(random.uniform(3, 6))
            
            logger.info("✅ 深度浏览完成")
            
        except Exception as e:
            logger.error(f"增强浏览失败: {str(e)}")
            # 降级到基础浏览
            self.fallback_browse_post(page)

    def fallback_browse_post(self, page):
        """降级浏览行为"""
        prev_url = None
        for _ in range(random.randint(8, 15)):
            # 更自然的滚动距离
            scroll_distance = random.randint(300, 800)
            page.run_js(f"window.scrollBy(0, {scroll_distance})")
            
            # 随机交互
            if random.random() < 0.3:
                self.simulate_user_interaction(page)
            
            # 检查是否到达底部
            at_bottom = page.run_js(
                "return (window.innerHeight + window.scrollY) >= document.body.scrollHeight"
            )
            current_url = page.url
            
            if current_url != prev_url:
                prev_url = current_url
            elif at_bottom and prev_url == current_url:
                logger.info("已到达页面底部")
                break

            # 动态等待时间
            wait_time = random.uniform(2, 5)
            time.sleep(wait_time)

    def simulate_user_interaction(self, page):
        """模拟用户交互行为"""
        try:
            interactions = [
                # 鼠标移动
                "document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: Math.random() * window.innerWidth, clientY: Math.random() * window.innerHeight }));",
                # 点击事件
                "document.dispatchEvent(new MouseEvent('click', { bubbles: true }));",
                # 滚动事件
                "window.dispatchEvent(new Event('scroll'));",
                # 焦点事件
                "document.dispatchEvent(new Event('focus'));"
            ]
            
            # 随机选择1-2个交互
            selected = random.sample(interactions, random.randint(1, 2))
            for js in selected:
                page.run_js(js)
                time.sleep(0.1)
                
        except Exception as e:
            logger.debug(f"模拟交互失败: {str(e)}")

    def trigger_statistical_events(self, page):
        """触发统计相关事件"""
        try:
            statistical_scripts = [
                # 触发页面浏览事件
                "window.dispatchEvent(new Event('pageview'));",
                # 触发自定义统计事件
                "window.dispatchEvent(new CustomEvent('userActivity', { detail: { type: 'pageview' } }));",
                # 模拟jQuery事件（如果存在）
                "if (typeof jQuery !== 'undefined') { jQuery(window).trigger('load'); jQuery(document).trigger('ready'); }",
                # 触发可见性事件
                "document.dispatchEvent(new Event('visibilitychange'));"
            ]
            
            for js in statistical_scripts:
                try:
                    page.run_js(js)
                except:
                    pass
                    
            time.sleep(1)
            logger.debug("📊 统计事件已触发")
            
        except Exception as e:
            logger.debug(f"触发统计事件失败: {str(e)}")

    def click_like(self, page):
        try:
            # 查找未点赞的按钮
            like_buttons = page.eles(".discourse-reactions-reaction-button")
            for button in like_buttons:
                try:
                    if button and button.states.is_enabled:
                        logger.info("找到未点赞按钮，准备点赞")
                        button.click()
                        time.sleep(random.uniform(1, 3))
                        logger.info("点赞成功")
                        return True
                except:
                    continue
            logger.info("未找到可点赞的按钮或已点过赞")
        except Exception as e:
            logger.error(f"点赞失败: {str(e)}")
        return False

    def run(self):
        if not self.login():
            logger.error("登录失败，程序终止")
            # 登录失败时清除缓存
            self.clear_caches()
            sys.exit(1)

        if BROWSE_ENABLED:
            self.click_topic()
            logger.info("✅ 浏览任务完成（统计优化版）")
            
            # 更新会话数据
            self.session_data['last_browse'] = datetime.now().isoformat()
            self.session_data['total_browsed'] = self.session_data.get('total_browsed', 0) + 1
            if not self.cache_saved:
                self.save_all_caches()

        self.print_connect_info()
        self.page.close()
        self.browser.quit()

    def print_connect_info(self):
        logger.info("获取连接信息")
        page = self.browser.new_tab()
        page.get("https://connect.linux.do/")
        time.sleep(3)
        
        try:
            rows = page.ele("tag:table").eles("tag:tr")
            info = []

            for row in rows:
                cells = row.eles("tag:td")
                if len(cells) >= 3:
                    project = cells[0].text.strip()
                    current = cells[1].text.strip()
                    requirement = cells[2].text.strip()
                    info.append([project, current, requirement])

            print("--------------Connect Info-----------------")
            print(tabulate(info, headers=["项目", "当前", "要求"], tablefmt="pretty"))
        except Exception as e:
            logger.error(f"获取连接信息失败: {str(e)}")
        
        page.close()


if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        print("Please set LINUXDO_USERNAME and LINUXDO_PASSWORD environment variables")
        exit(1)
    
    logger.info("🚀 LinuxDo 自动化脚本启动 (缓存优化版)")
    browser = LinuxDoBrowser()
    browser.run()
    logger.info("🔚 脚本执行完成")
