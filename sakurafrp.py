import logging
import os
import time
import random
import json
import re
from typing import Optional, Dict, Tuple
from dataclasses import dataclass

from seleniumwire import webdriver
from selenium.webdriver import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from openai import OpenAI

# 尝试加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('checkin.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class Config:
    """配置数据类"""
    sakurafrp_user: str
    sakurafrp_pass: str
    base_url: str
    api_key: str
    model: str
    chrome_binary_path: Optional[str] = None
    
    @classmethod
    def from_env(cls) -> 'Config':
        """从环境变量加载配置"""
        def get_env(key: str, required: bool = True) -> str:
            value = os.environ.get(key, "").split('\n')[0].strip()
            if required and not value:
                raise ValueError(f"环境变量 {key} 未设置或为空")
            return value
        
        return cls(
            sakurafrp_user=get_env("SAKURAFRP_USER"),
            sakurafrp_pass=get_env("SAKURAFRP_PASS"),
            base_url=get_env("BASE_URL"),
            api_key=get_env("API_KEY"),
            model=get_env("MODEL"),
            chrome_binary_path=get_env("CHROME_BINARY_PATH", required=False)
        )


class WebDriverManager:
    """WebDriver 管理器"""
    
    def __init__(self, config: Config):
        self.config = config
        self.driver = None
    
    def initialize(self, headless: bool = False) -> Optional[webdriver.Chrome]:
        """初始化 Selenium-Wire WebDriver"""
        logger.info("正在初始化 Selenium-Wire WebDriver...")
        
        # 配置 selenium-wire 以捕获请求
        wire_options = {
            'disable_capture': False,  # 启用请求捕获
            'disable_encoding': True,   # 禁用内容编码以便读取响应
        }
        
        ops = Options()
        ops.add_experimental_option("detach", not headless)
        ops.add_argument('--window-size=1280,800')
        ops.add_argument('--disable-blink-features=AutomationControlled')
        ops.add_argument('--no-proxy-server')
        ops.add_argument('--lang=zh-CN')
        ops.add_argument('--disable-gpu')
        ops.add_argument('--no-sandbox')
        ops.add_argument('--disable-dev-shm-usage')  # 解决 Docker/CI 环境内存问题
        
        # GitHub Actions 环境必须使用 headless 模式
        if headless or os.getenv('CI') == 'true':
            logger.info("检测到 CI 环境或 headless 模式，启用无头浏览器")
            ops.add_argument('--headless=new')  # 使用新的 headless 模式
            ops.add_argument('--disable-software-rasterizer')
        
        # 设置自定义 Chrome 路径
        if self.config.chrome_binary_path and os.path.exists(self.config.chrome_binary_path):
            logger.info(f"使用自定义 Chrome 路径: {self.config.chrome_binary_path}")
            ops.binary_location = self.config.chrome_binary_path
        
        try:
            # 在 CI 环境中，chromedriver 通常已安装在系统路径
            if os.getenv('CI') == 'true':
                logger.info("CI 环境中使用系统 ChromeDriver")
                self.driver = webdriver.Chrome(
                    options=ops,
                    seleniumwire_options=wire_options
                )
            else:
                # 本地环境使用项目目录中的 chromedriver
                local_driver_path = os.path.abspath("chromedriver.exe")
                if not os.path.exists(local_driver_path):
                    logger.error("未找到 chromedriver.exe，请确保文件在项目目录中")
                    return None
                
                logger.info(f"使用本地驱动: {local_driver_path}")
                service = Service(executable_path=local_driver_path)
                self.driver = webdriver.Chrome(
                    service=service,
                    options=ops,
                    seleniumwire_options=wire_options
                )
            
            logger.info("WebDriver 初始化成功")
            return self.driver
            
        except Exception as e:
            logger.error(f"WebDriver 初始化失败: {e}", exc_info=True)
            return None
    
    def close(self):
        """关闭 WebDriver"""
        if self.driver:
            self.driver.quit()
            logger.info("WebDriver 已关闭")


class HumanSimulator:
    """模拟人类行为"""
    
    @staticmethod
    def type_text(element, text: str, min_delay: float = 0.05, max_delay: float = 0.2):
        """模拟人类打字"""
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(min_delay, max_delay))
    
    @staticmethod
    def random_sleep(min_sec: float = 1.0, max_sec: float = 3.0):
        """随机等待"""
        time.sleep(random.uniform(min_sec, max_sec))


class CaptchaHandler:
    """验证码处理器"""
    
    def __init__(self, config: Config):
        self.config = config
        self.client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key
        )
        self.max_retries = 3  # 最大重试次数
    
    def handle_geetest_captcha(self, driver, wait: WebDriverWait) -> bool:
        """处理 GeeTest 九宫格验证码（带重试机制）"""
        logger.info("开始处理 GeeTest 验证码...")
        
        for attempt in range(1, self.max_retries + 1):
            logger.info(f"验证码尝试 {attempt}/{self.max_retries}")
            
            try:
                # 等待验证码窗口出现
                wait.until(EC.visibility_of_element_located((By.CLASS_NAME, "geetest_widget")))
                logger.info("检测到 GeeTest 验证码窗口")
                
                # 获取验证码图片
                captcha_img_element = wait.until(
                    EC.visibility_of_element_located((By.CLASS_NAME, "geetest_item_img"))
                )
                img_url = captcha_img_element.get_attribute('src')
                
                if not img_url:
                    logger.error("无法提取验证码图片 URL")
                    continue
                
                logger.info("成功获取验证码图片 URL")
                
                # 调用视觉模型识别
                recognition_result = self._recognize_captcha(img_url)
                if not recognition_result:
                    logger.warning("识别失败，尝试刷新验证码...")
                    self._refresh_captcha(driver)
                    time.sleep(2)
                    continue
                
                logger.info(f"验证码识别结果: {recognition_result}")
                
                # 根据识别结果点击相应的九宫格
                if not self._click_captcha_items(driver, recognition_result):
                    logger.error("点击验证码失败，尝试刷新...")
                    self._refresh_captcha(driver)
                    time.sleep(2)
                    continue
                
                # 等待验证结果
                logger.info("等待验证结果...")
                verification_result = self._wait_for_verification_result(driver)
                
                if verification_result == "success":
                    logger.info("✓ 验证码验证成功！")
                    return True
                elif verification_result == "fail":
                    logger.warning(f"✗ 验证失败（第 {attempt} 次尝试），刷新验证码重试...")
                    self._refresh_captcha(driver)
                    time.sleep(2)
                else:
                    logger.info("验证码窗口已关闭，假设验证成功")
                    return True
                
            except TimeoutException:
                logger.info("未检测到 GeeTest 验证码窗口")
                return False
            except Exception as e:
                logger.error(f"处理验证码时发生错误: {e}", exc_info=True)
                if attempt < self.max_retries:
                    logger.info("尝试刷新验证码...")
                    try:
                        self._refresh_captcha(driver)
                        time.sleep(2)
                    except:
                        pass
        
        logger.error(f"验证码验证失败，已达到最大重试次数 ({self.max_retries})")
        return False
    
    def _recognize_captcha(self, img_url: str) -> Optional[Dict]:
        """使用视觉模型识别验证码"""
        try:
            prompt = (
                '这是一个九宫格验证码，请按从左到右、从上到下的顺序识别每个格子里的物品名称，'
                '最后识别左下角的参考图。输出格式为JSON：{"1":"名称", "2":"名称", ..., "10":"参考图名称"}。'
                '名称要简洁，参考图名称必须是九宫格里已有的名称。若有类似物品（如气球与热气球），请统一名称。'
            )
            
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=[{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt},
                        {'type': 'image_url', 'image_url': {'url': img_url}}
                    ]
                }],
                stream=False
            )
            
            result_content = response.choices[0].message.content
            logger.info(f"模型原始输出: {result_content}")
            
            # 清理并解析 JSON
            cleaned_str = result_content.replace("'", '"')
            # 尝试提取 JSON 内容（处理可能包含其他文本的情况）
            json_match = json.loads(cleaned_str) if cleaned_str.startswith('{') else None
            
            if not json_match:
                logger.error("无法从模型输出中提取有效 JSON")
                return None
            
            return json_match
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"验证码识别失败: {e}", exc_info=True)
            return None
    
    def _click_captcha_items(self, driver, recognition_result: Dict) -> bool:
        """
        根据识别结果点击九宫格中匹配的格子
        
        九宫格布局（索引从1开始）：
        1  2  3
        4  5  6
        7  8  9
        
        第10个是参考图（左下角）
        """
        try:
            # 获取参考图名称（第10个元素）
            target_name = recognition_result.get("10", "").strip()
            if not target_name:
                logger.error("未能从识别结果中获取参考图名称")
                return False
            
            logger.info(f"目标物品: {target_name}")
            
            # 获取所有九宫格元素（前9个）
            grid_items = driver.find_elements(By.CLASS_NAME, "geetest_item")
            
            # 排除最后一个（参考图），只处理前9个
            if len(grid_items) < 9:
                logger.error(f"九宫格元素数量不足，只找到 {len(grid_items)} 个")
                return False
            
            clickable_items = grid_items[:9]
            
            # 遍历前9个格子，找到匹配的物品并点击
            clicked_count = 0
            for i in range(9):
                position = i + 1  # 位置索引从1开始
                item_name = recognition_result.get(str(position), "").strip()
                
                logger.info(f"位置 {position}: {item_name}")
                
                # 如果当前格子的物品名称匹配参考图
                if item_name and item_name == target_name:
                    logger.info(f"找到匹配项！位置 {position} - {item_name}")
                    
                    # 点击该格子
                    try:
                        # 使用 JavaScript 点击，更稳定
                        driver.execute_script("arguments[0].click();", clickable_items[i])
                        clicked_count += 1
                        logger.info(f"已点击位置 {position}")
                        
                        # 点击后短暂等待，模拟人类操作
                        time.sleep(random.uniform(0.3, 0.6))
                        
                    except Exception as e:
                        logger.error(f"点击位置 {position} 时出错: {e}")
            
            if clicked_count == 0:
                logger.warning(f"未找到匹配 '{target_name}' 的格子")
                return False
            
            logger.info(f"共点击了 {clicked_count} 个匹配的格子")
            
            # 点击完成后，查找并点击确认按钮
            try:
                # 等待确认按钮变为可用状态（移除 geetest_disable 类）
                confirm_button = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "geetest_commit"))
                )
                
                # 检查按钮是否可用（没有 geetest_disable 类）
                button_classes = confirm_button.get_attribute("class")
                logger.info(f"确认按钮状态: {button_classes}")
                
                # 等待按钮变为可点击状态（最多等待3秒）
                max_wait = 3
                start = time.time()
                while "geetest_disable" in confirm_button.get_attribute("class"):
                    if time.time() - start > max_wait:
                        logger.warning("确认按钮未激活，但仍尝试点击")
                        break
                    time.sleep(0.2)
                    confirm_button = driver.find_element(By.CLASS_NAME, "geetest_commit")
                
                logger.info("找到确认按钮，准备点击...")
                driver.execute_script("arguments[0].click();", confirm_button)
                logger.info("已点击确认按钮")
                time.sleep(1)
            except TimeoutException:
                logger.info("未找到确认按钮，可能自动提交")
            
            return True
            
        except Exception as e:
            logger.error(f"点击验证码格子时发生错误: {e}", exc_info=True)
            return False
    
    def _refresh_captcha(self, driver) -> bool:
        """刷新验证码"""
        try:
            logger.info("正在刷新验证码...")
            refresh_button = driver.find_element(By.CLASS_NAME, "geetest_refresh")
            driver.execute_script("arguments[0].click();", refresh_button)
            logger.info("已点击刷新按钮")
            time.sleep(1.5)  # 等待新验证码加载
            return True
        except Exception as e:
            logger.error(f"刷新验证码失败: {e}")
            return False
    
    def _wait_for_verification_result(self, driver, timeout: int = 10) -> str:
        """
        等待并检测验证结果（通过监听网络请求）
        
        返回值:
            "success": 验证成功
            "fail": 验证失败
            "closed": 验证码窗口已关闭
            "timeout": 超时
        """
        try:
            logger.info("监听验证结果...")
            start_time = time.time()
            
            # 清除之前的请求记录，只监听新的请求
            del driver.requests
            
            while time.time() - start_time < timeout:
                # 检查网络请求
                for request in driver.requests:
                    if request.response and 'api.geevisit.com/ajax.php' in request.url:
                        try:
                            # 获取响应内容
                            response_body = request.response.body.decode('utf-8')
                            logger.info(f"捕获到验证API响应: {response_body[:200]}")
                            
                            # 解析 JSONP 响应：geetest_xxx({"status": "success", ...})
                            json_match = re.search(r'geetest_\d+\((.*)\)', response_body)
                            if json_match:
                                json_str = json_match.group(1)
                                result_data = json.loads(json_str)
                                
                                status = result_data.get('status')
                                if status == 'success':
                                    data = result_data.get('data', {})
                                    result = data.get('result', '')
                                    
                                    if result == 'success':
                                        logger.info("✓ API返回验证成功")
                                        return "success"
                                    elif result == 'fail':
                                        logger.warning("✗ API返回验证失败")
                                        return "fail"
                            
                        except Exception as e:
                            logger.debug(f"解析响应时出错: {e}")
                
                # 同时检查验证码窗口是否关闭
                try:
                    widget = driver.find_element(By.CLASS_NAME, "geetest_widget")
                    if not widget.is_displayed():
                        logger.info("验证码窗口已关闭")
                        return "closed"
                except:
                    logger.info("验证码窗口未找到")
                    return "closed"
                
                time.sleep(0.5)
            
            logger.warning(f"验证结果等待超时 ({timeout}秒)")
            return "timeout"
            
        except Exception as e:
            logger.error(f"等待验证结果时出错: {e}", exc_info=True)
            return "timeout"


class CheckInAutomation:
    """签到自动化主类"""
    
    def __init__(self, config: Config):
        self.config = config
        self.driver_manager = WebDriverManager(config)
        self.captcha_handler = CaptchaHandler(config)
        self.simulator = HumanSimulator()
    
    def run(self):
        """执行签到流程"""
        # GitHub Actions 环境自动使用 headless 模式
        headless = os.getenv('CI') == 'true' or os.getenv('HEADLESS', 'false').lower() == 'true'
        
        driver = self.driver_manager.initialize(headless=headless)
        if not driver:
            logger.error("WebDriver 初始化失败，无法继续")
            return
        
        wait = WebDriverWait(driver, 20)
        
        try:
            # 步骤1: 登录
            if not self._login(driver, wait):
                logger.error("登录失败")
                return
            
            # 步骤2: 跳转到 SakuraFrp
            if not self._navigate_to_sakurafrp(driver, wait):
                logger.error("跳转到 SakuraFrp 失败")
                return
            
            # 步骤3: 执行签到
            if not self._perform_checkin(driver, wait):
                logger.error("签到失败")
                return
            
            logger.info("✓ 签到流程完成")
            
        except Exception as e:
            logger.error(f"执行过程中发生错误: {e}", exc_info=True)
        finally:
            logger.info("脚本执行完毕，浏览器保持打开状态供检查")
    
    def _login(self, driver, wait: WebDriverWait) -> bool:
        """执行登录"""
        login_url = "https://openid.13a.com/login"
        logger.info(f"导航到登录页面: {login_url}")
        driver.get(login_url)
        
        try:
            # 输入用户名和密码
            username_input = wait.until(EC.visibility_of_element_located((By.ID, 'username')))
            password_input = wait.until(EC.visibility_of_element_located((By.ID, 'password')))
            
            logger.info("输入登录凭据...")
            username_input.clear()
            self.simulator.type_text(username_input, self.config.sakurafrp_user)
            password_input.clear()
            self.simulator.type_text(password_input, self.config.sakurafrp_pass)
            
            # 点击登录按钮
            login_button = wait.until(EC.element_to_be_clickable((By.ID, 'login')))
            logger.info("点击登录按钮...")
            driver.execute_script("arguments[0].click();", login_button)
            
            self.simulator.random_sleep(3, 5)
            logger.info("登录成功")
            return True
            
        except TimeoutException:
            logger.error("登录页面元素加载超时")
            return False
        except Exception as e:
            logger.error(f"登录过程出错: {e}", exc_info=True)
            return False
    
    def _navigate_to_sakurafrp(self, driver, wait: WebDriverWait) -> bool:
        """跳转到 SakuraFrp 仪表板"""
        try:
            # 点击 SakuraFrp 链接
            sakura_link = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//div[@class='action-list']/a[contains(., 'Sakura Frp')]")
                )
            )
            logger.info("点击 SakuraFrp 跳转链接...")
            sakura_link.click()
            self.simulator.random_sleep(2, 4)
            
            # 处理年龄确认弹窗（如果存在）
            try:
                age_confirm = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//div[@class='yes']/a[contains(text(), '是，我已满18岁')]")
                    )
                )
                logger.info("处理年龄确认弹窗...")
                age_confirm.click()
                self.simulator.random_sleep(2, 3)
            except TimeoutException:
                logger.info("未检测到年龄确认弹窗")
            
            logger.info("成功跳转到 SakuraFrp 仪表板")
            return True
            
        except TimeoutException:
            logger.warning("SakuraFrp 跳转链接未找到，可能已在目标页面")
            return True
        except Exception as e:
            logger.error(f"跳转过程出错: {e}", exc_info=True)
            return False
    
    def _perform_checkin(self, driver, wait: WebDriverWait) -> bool:
        """执行签到操作"""
        try:
            # 查找签到按钮
            check_in_button = None
            try:
                check_in_button = wait.until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[./span[contains(text(),'点击这里签到')]]")
                    )
                )
                logger.info("找到签到按钮")
            except TimeoutException:
                # 检查是否已签到
                try:
                    WebDriverWait(driver, 2).until(
                        EC.visibility_of_element_located(
                            (By.XPATH, "//p[contains(., '今天已经签到过啦')]")
                        )
                    )
                    logger.info("今日已签到")
                    return True
                except TimeoutException:
                    logger.error("未找到签到按钮或已签到标识")
                    return False
            
            # 点击签到按钮
            if check_in_button:
                logger.info("点击签到按钮...")
                driver.execute_script("arguments[0].click();", check_in_button)
                self.simulator.random_sleep(2, 4)
                
                # 处理验证码
                captcha_result = self.captcha_handler.handle_geetest_captcha(driver, wait)
                if captcha_result:
                    logger.info("验证码处理完成")
                    return True
                else:
                    logger.warning("验证码处理失败或未出现验证码")
                    return False
            
            return False
            
        except Exception as e:
            logger.error(f"签到过程出错: {e}", exc_info=True)
            return False


def main():
    """主函数"""
    try:
        # 加载配置
        config = Config.from_env()
        logger.info(f"使用账户: {config.sakurafrp_user}")
        
        # 执行自动签到
        automation = CheckInAutomation(config)
        automation.run()
        
    except ValueError as e:
        logger.error(f"配置错误: {e}")
    except Exception as e:
        logger.error(f"程序执行失败: {e}", exc_info=True)


if __name__ == "__main__":
    main()