from pymongo import MongoClient
from datetime import datetime
from gtts import gTTS
import os
import uuid
import io
from flask import Flask, render_template, request, jsonify
import requests
import boto3
from botocore.config import Config

app = Flask(__name__)

# 连接 MongoDB
client = MongoClient("mongodb+srv://serena001:3z2nWuaTwsmzACiA@cluster0.0i1vd0p.mongodb.net/?appName=Cluster0")
db = client["language_learning"]
translations_collection = db["translations"]

# Cloudflare R2 配置
R2_ACCESS_KEY = "e845c1c72d740734530aeae6ed3effd7"
R2_SECRET_KEY = "a1295b071ca02e8bb96795c490d3532527d293c67bab9717fb648bbe9b1a2314"
R2_ENDPOINT = "https://1a39ac88a3907457d9e32189ce681ab5.r2.cloudflarestorage.com"
R2_BUCKET = "serena-audio"
R2_PUBLIC_URL = "https://audio.lalsystem.org"  # 新增：公开访问域名

# 创建 R2 客户端
s3_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    config=Config(signature_version='s3v4')
)

DEEPSEEK_API_KEY = "sk-e70a027b756148ee9887c853d780fbbe"

def upload_audio_to_r2(tts_object, filename):
    """上传音频到 R2 并返回 URL"""
    audio_buffer = io.BytesIO()
    tts_object.write_to_fp(audio_buffer)
    audio_buffer.seek(0)
    
    s3_client.put_object(
        Bucket=R2_BUCKET,
        Key=filename,
        Body=audio_buffer.getvalue(),
        ContentType='audio/mpeg'
    )
    
    # 修改：使用公开域名返回 URL
    return f"{R2_PUBLIC_URL}/{filename}"

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/translate", methods=["POST"])
def translate():
    data = request.get_json()
    text = data["text"]
    source = data["source"]
    target = data["target"]
    
    # 翻译句子
    response = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "user", "content": f"把下面这句话从{source}翻译成{target}，只返回翻译结果，不要解释：{text}"}
            ]
        }
    )
    
    result = response.json()
    translation = result["choices"][0]["message"]["content"]
    
    # 生成句子音频并上传到 R2
    tts = gTTS(text=translation, lang=target)
    sentence_filename = f"sentence_{uuid.uuid4()}.mp3"
    sentence_audio_url = upload_audio_to_r2(tts, sentence_filename)
    
    # 同时保存本地一份（网页播放用）
    audio_path = "static/audio/sentence.mp3"
    tts.save(audio_path)
    
    # 分解单词
    words = translation.replace(",", "").replace(".", "").replace("!", "").replace("?", "").split()
    
    word_list = []
    for i, word in enumerate(words):
        # 单词翻译回母语
        word_response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "user", "content": f"把这个{target}单词翻译成{source}，只返回翻译结果：{word}"}
                ]
            }
        )
        word_result = word_response.json()
        word_translation = word_result["choices"][0]["message"]["content"]
        
        # 生成单词音频并上传到 R2
        word_tts = gTTS(text=word, lang=target)
        word_filename = f"word_{uuid.uuid4()}.mp3"
        word_audio_url = upload_audio_to_r2(word_tts, word_filename)
        
        # 同时保存本地
        word_audio_path = f"static/audio/word_{i}.mp3"
        word_tts.save(word_audio_path)
        
        word_list.append({
            "word": word,
            "translation": word_translation,
            "audio": "/" + word_audio_path,
            "audio_r2": word_audio_url
        })
    
    # 保存到数据库
    translations_collection.insert_one({
        "source_text": text,
        "source_lang": source,
        "target_lang": target,
        "translation": translation,
        "sentence_audio_r2": sentence_audio_url,
        "words": word_list,
        "timestamp": datetime.now()
    })

    return jsonify({
        "translation": translation,
        "audio": "/" + audio_path,
        "words": word_list
    })

@app.route("/history")
def history():
    return render_template("history.html")

@app.route("/api/history", methods=["GET"])
def get_history():
    # 从数据库获取所有翻译记录，按时间倒序
    records = list(translations_collection.find().sort("timestamp", -1))
    
    # 把 MongoDB 的 ObjectId 转成字符串
    for record in records:
        record["_id"] = str(record["_id"])
        record["timestamp"] = record["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
    
    return jsonify(records)

@app.route("/api/history/<record_id>", methods=["DELETE"])
def delete_record(record_id):
    from bson import ObjectId
    translations_collection.delete_one({"_id": ObjectId(record_id)})
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(debug=True)