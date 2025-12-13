import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime


def send_log_email(log_file='checkin.log'):
    """
    发送签到日志邮件
    
    Args:
        log_file: 日志文件路径
    """
    # 从环境变量读取配置
    smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    sender_email = os.getenv('EMAIL_USERNAME', '')
    sender_password = os.getenv('EMAIL_PASSWORD', '')
    receiver_email = os.getenv('RECEIVER_EMAIL', sender_email)
    
    # 检查配置
    if not sender_email or not sender_password:
        print("❌ 邮件配置未设置，跳过发送")
        return False
    
    try:
        # 读取日志内容
        log_content = ""
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8') as f:
                log_content = f.read()
        else:
            log_content = "日志文件不存在"
        
        # 判断签到是否成功（根据日志内容）
        is_success = "签到流程完成" in log_content or "验证码验证成功" in log_content
        status_emoji = "✅" if is_success else "❌"
        status_text = "成功" if is_success else "失败"
        
        # 创建邮件
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = receiver_email
        msg['Subject'] = f"{status_emoji} SakuraFrp 签到{status_text} - {datetime.now().strftime('%Y-%m-%d')}"
        
        # 邮件正文
        body = f"""
SakuraFrp 自动签到报告

状态: {status_emoji} {status_text}
时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{'='*50}
日志内容:
{'='*50}

{log_content[-2000:] if len(log_content) > 2000 else log_content}

{'='*50}
此邮件由自动签到系统发送
        """
        
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # 添加日志附件
        if os.path.exists(log_file):
            with open(log_file, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename= {os.path.basename(log_file)}')
            msg.attach(part)
        
        # 发送邮件
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        
        print(f"✅ 邮件发送成功: {receiver_email}")
        return True
        
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")
        return False


if __name__ == "__main__":
    send_log_email()