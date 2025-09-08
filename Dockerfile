# Dùng Python 3.11
FROM python:3.11-slim

# Tạo thư mục app
WORKDIR /app

# Copy requirements và cài đặt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ code
COPY . .

# Cổng cho Flask hoặc webhook (Render yêu cầu expose)
EXPOSE 10000

# Chạy bot
CMD ["python", "heli_bot.py"]
