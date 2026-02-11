import logging
import os
import time
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from config import Config

logger = logging.getLogger(__name__)


class CheckInAutomation:
    """签到自动化主类"""
    def __init__(self, config: Config):
        self.config = config
        try:
            from webdriver_manager import WebDriverManager
        except ImportError:
            from webdriver_manager import WebDriverManager  # 如果模块名不同
        self.driver_manager = WebDriverManager(config)
        try:
            from captcha_handler import CaptchaHandler
        except ImportError:
            from captcha_handler import CaptchaHandler  # 如果模块名不同
        self.captcha_handler = CaptchaHandler(config)
        try:
            from human_simulator import HumanSimulator
        except ImportError:
            from human_simulator import HumanSimulator  # 如果模块名不同
        self.simulator = HumanSimulator()
        self.max_retries = config.max_retries
    
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
            
            # 步骤2: 跳转到 处理年龄
            if not self._navigate_to_sakurafrp(driver, wait):
                logger.error("跳转到 SakuraFrp 失败")
                return
            
            # 步骤3: 执行签到
            if not self._perform_checkin(driver, wait):
                logger.error("签到失败")
                driver.save_screenshot('error_screenshot.png')
                with open('error_page_source.html', 'w', encoding='utf-8') as f:
                    f.write(driver.page_source)
                return
            
            logger.info("✓ 签到流程完成")
            
        except Exception as e:
            logger.error(f"执行过程中发生错误: {e}", exc_info=True)
        finally:
            logger.info("脚本执行完毕，浏览器保持打开状态供检查")
    
    def _login(self, driver, wait: WebDriverWait) -> bool:
        """执行登录"""
        login_url = "https://www.natfrp.com/user/"
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
            # sakura_link = wait.until(
            #     EC.element_to_be_clickable(
            #         (By.XPATH, "//div[@class='action-list']/a[contains(., 'Sakura Frp')]")
            #     )
            # )
            # logger.info("点击 SakuraFrp 跳转链接...")
            # sakura_link.click()
            # self.simulator.random_sleep(2, 4)
            
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
        for attempt in range(1, self.max_retries+1):
            logger.info(f"验证码尝试 {attempt}/{self.max_retries}")
            try:
                # 查找签到按钮
                check_in_button = None
                try:
                    check_in_button = wait.until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//button[.//span[contains(., '点击这里签到')]]")
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
                    driver.refresh()
                    time.sleep(5)
                    continue

                return False
                
            except Exception as e:
                logger.error(f"签到过程出错: {e}", exc_info=True)
                return False
        logger.info("已达到最大重试次数")
        return False
