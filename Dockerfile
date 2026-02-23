# Utilitzem una versió lleugera de Python
FROM python:3.9-slim

# Creem la carpeta de treball
WORKDIR /app

# Copiem els requisits i els instal·lem
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiem la resta del codi
COPY . .

# Obrim el port 7860 (És el port OBLIGATORI que demana Hugging Face)
EXPOSE 7860

# La comanda per arrencar el servidor
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]