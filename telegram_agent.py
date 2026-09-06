import base64
import json
import os
import threading
from io import BytesIO
from anthropic import Anthropic
from flask import Flask
import openpyxl
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# -------------------------------------------------------------
# 🌐 Render 무료 Web Service 포트 에러(404 / No open ports) 완벽 방지용 웹 서버
# -------------------------------------------------------------
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Telegram AI Bot is Running 24/7!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()
# -------------------------------------------------------------

# 1. 환경 변수 읽기
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_DRIVE_CREDENTIALS", "")

# 2. Google Drive API 클라이언트 초기화
drive_service = None
if GOOGLE_CREDENTIALS_JSON:
    try:
        creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        scopes = ["https://www.googleapis.com/auth/drive.file"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        drive_service = build("drive", "v3", credentials=creds)
        print("📁 Google Drive API 연결 성공!")
    except Exception as e:
        print(f"⚠️ Google Drive 연결 실패: {e}")
        print("=" * 40)
        print("=== ANTHROPIC API ERROR DETAILS ===")
        print(f"Error Type: {type(e)}")
        print(f"Error Message: {e}")
        print("=" * 40)
        # 기존 답장 처리 문구


client = Anthropic(api_key=ANTHROPIC_API_KEY)
MODEL_NAME = "claude-3-haiku-20240307"


def get_or_create_drive_file_id(filename="chat_history.json"):
    """구글 드라이브에서 특정 파일의 ID를 찾거나 없으면 생성"""
    if not drive_service:
        return None
    try:
        query = f"name = '{filename}' and trashed = false"
        results = drive_service.files().list(q=query, fields="files(id, name)").execute()
        files = results.get("files", [])
        if files:
            return files[0]["id"]
    except Exception as e:
        print(f"[Drive Search Error]: {e}")
    return None


def load_history(user_id: str):
    """구글 드라이브에서 대화 기록 불러오기"""
    if drive_service:
        file_id = get_or_create_drive_file_id()
        if file_id:
            try:
                request = drive_service.files().get_media(fileId=file_id)
                fh = BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                fh.seek(0)
                content = fh.read().decode("utf-8")
                all_data = json.loads(content)
                return all_data.get(user_id, [])
            except Exception as e:
                print(f"[Drive Load Error]: {e}")

    # Fallback: 로컬 파일
    if os.path.exists("chat_history.json"):
        try:
            with open("chat_history.json", "r", encoding="utf-8") as f:
                return json.load(f).get(user_id, [])
        except Exception:
            return []
    return []


def save_history(user_id: str, user_history: list):
    """구글 드라이브에 대화 기록 저장하기"""
    all_data = {}
    
    if drive_service:
        file_id = get_or_create_drive_file_id()
        if file_id:
            try:
                request = drive_service.files().get_media(fileId=file_id)
                fh = BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                fh.seek(0)
                all_data = json.loads(fh.read().decode("utf-8"))
            except Exception:
                all_data = {}

    all_data[user_id] = user_history[-30:]  # 최근 30개 대화 유지
    json_bytes = json.dumps(all_data, ensure_ascii=False, indent=2).encode("utf-8")

    if drive_service:
        file_id = get_or_create_drive_file_id()
        media = MediaIoBaseUpload(BytesIO(json_bytes), mimetype="application/json", resumable=True)
        try:
            if file_id:
                drive_service.files().update(fileId=file_id, media_body=media).execute()
            else:
                file_metadata = {"name": "chat_history.json", "mimeType": "application/json"}
                drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
            return
        except Exception as e:
            print(f"[Drive Save Error]: {e}")

    try:
        with open("chat_history.json", "w", encoding="utf-8") as f:
            f.write(json_bytes.decode("utf-8"))
    except Exception as e:
        print(f"[Local Save Error]: {e}")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """대화 기록 초기화 명령어 (/reset)"""
    user_id = str(update.effective_user.id)
    save_history(user_id, [])

    print(f"\n[시스템]: 사용자({user_id}) 대화 기억 초기화 완료.")
    await update.message.reply_text(
        "🧹 제이스님과의 대화 기억이 구글 드라이브에서 깨끗하게 초기화되었습니다!"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_text = update.message.text
    print(f"\n[사용자 텍스트 요청]: {user_text}")

    user_history = load_history(user_id)
    user_history.append({"role": "user", "content": user_text})
    recent_messages = user_history[-20:]

    try:
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=2048,
            system=(
                "당신은 유능하고 정중한 개인 업무용 AI 비서입니다. "
                "사용자의 이름은 '제이스'입니다. 제이스님과의 이전 업무 맥락과 대화 기록을 잘 기억하여 명확하게 답변해 주세요."
            ),
            messages=recent_messages,
        )

        ai_reply = response.content[0].text
        user_history.append({"role": "assistant", "content": ai_reply})
        save_history(user_id, user_history)

    except Exception as e:
        error_msg = str(e)
        print(f"[에러 상세 정보]: {error_msg}")
        ai_reply = f"오류가 발생했습니다: {error_msg}"

    await update.message.reply_text(ai_reply)
    print(f"[AI 답장 전송 완료]")


async def handle_photo_or_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user_id = str(update.effective_user.id)
    caption = update.message.caption or "이 사진 또는 문서의 글자와 내용을 읽고 자세히 정리해 주세요."
    print(f"\n[사용자 파일 전송]: {caption}")

    status_msg = await update.message.reply_text(
        "🔍 문서/이미지를 시각적으로 분석하고 있습니다. 잠시만 기다려 주세요..."
    )

    try:
        if update.message.photo:
            file_obj = await update.message.photo[-1].get_file()
            mime_type = "image/jpeg"
        elif update.message.document:
            file_obj = await update.message.document.get_file()
            mime_type = update.message.document.mime_type or "image/jpeg"
        else:
            await status_msg.edit_text("처리할 수 없는 파일 형식입니다.")
            return

        file_bytes = await file_obj.download_as_bytearray()
        base64_image = base64.b64encode(file_bytes).decode("utf-8")

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type if "image" in mime_type else "image/jpeg",
                            "data": base64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"{caption}\n\n"
                            "만약 사진이나 문서에 표 데이터(숫자, 데이터 목록, 영수증 내역 등)가 포함되어 있다면, "
                            "JSON 코드 블록으로 아래와 같이 표 데이터를 정리해 주세요.\n"
                            "예시:\n"
                            "```json\n"
                            "{\n"
                            '  "table_data": [\n'
                            '    ["항목", "금액", "비고"],\n'
                            '    ["A제품", "10000", "정상"],\n'
                            '    ["B제품", "20000", "할인"]\n'
                            "  ]\n"
                            "}\n"
                            "```\n"
                            "JSON 뒤에는 사용자가 이해하기 쉬운 텍스트 요약 설명을 첨부해 주세요."
                        ),
                    },
                ],
            }
        ]

        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=3000,
            system="당신은 문서 및 이미지 데이터 분석 전문 AI 비서입니다.",
            messages=messages,
        )

        ai_reply = response.content[0].text

        if "```json" in ai_reply and '"table_data"' in ai_reply:
            try:
                json_str = ai_reply.split("```json")[1].split("```")[0].strip()
                parsed_data = json.loads(json_str)

                if "table_data" in parsed_data:
                    table_rows = parsed_data["table_data"]

                    wb = openpyxl.Workbook()
                    ws = wb.active
                    ws.title = "추출된_데이터"

                    for row in table_rows:
                        ws.append(row)

                    excel_stream = BytesIO()
                    wb.save(excel_stream)
                    excel_stream.seek(0)

                    await update.message.reply_document(
                        document=excel_stream,
                        filename="extracted_data.xlsx",
                        caption="📊 문서에서 추출된 데이터로 작성한 엑셀 파일입니다.",
                    )
            except Exception as json_err:
                print(f"[엑셀 변환 중 경고]: {json_err}")

        user_history = load_history(user_id)
        user_history.append({"role": "user", "content": f"[사진/문서 분석 요청]: {caption}"})
        user_history.append({"role": "assistant", "content": ai_reply})
        save_history(user_id, user_history)

        await status_msg.edit_text(ai_reply)
        print("[이미지/문서 분석 및 구글 드라이브 기록 완료]")

    except Exception as e:
        error_msg = str(e)
        print(f"[에러 발생]: {error_msg}")
        await status_msg.edit_text(f"⚠️ 파일 처리 중 오류 발생: {error_msg}")


if __name__ == "__main__":
    print("🤖 [구글 드라이브 동기화 연동] Claude 3.5 Sonnet 가동 중...")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    )

    app.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.ALL, handle_photo_or_document)
    )

    app.run_polling()
