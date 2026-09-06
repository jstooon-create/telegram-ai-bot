import base64
import json
import os
from io import BytesIO
from anthropic import Anthropic
import openpyxl
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# 1. 환경 변수 읽기 (로컬 테스트 시 콤마 뒤의 실제 키/토큰 값을 사용)
ANTHROPIC_API_KEY = os.environ.get(
    "ANTHROPIC_API_KEY", ""
)
TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", ""
)
# API 키 및 토큰 유효성 검사
if not ANTHROPIC_API_KEY or not TELEGRAM_BOT_TOKEN:
    print("⚠️ 경고: ANTHROPIC_API_KEY 또는 TELEGRAM_BOT_TOKEN이 설정되지 않았습니다.")

client = Anthropic(api_key=ANTHROPIC_API_KEY)
MODEL_NAME = "claude-3-5-sonnet-20241022"

HISTORY_FILE = "chat_history.json"


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[히스토리 로드 오류]: {e}")
            return {}
    return {}


def save_history(history_data):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[히스토리 저장 오류]: {e}")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    history_data = load_history()

    if user_id in history_data:
        del history_data[user_id]
        save_history(history_data)

    print(f"\n[시스템]: 사용자({user_id}) 대화 기억 초기화 완료.")
    await update.message.reply_text(
        "🧹 대화 기억이 깨끗하게 초기화되었습니다! 새롭게 대화를 시작해 주세요."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_text = update.message.text
    print(f"\n[사용자 텍스트 요청]: {user_text}")

    history_data = load_history()
    user_history = history_data.get(user_id, [])

    user_history.append({"role": "user", "content": user_text})
    recent_messages = user_history[-20:]

    try:
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=2048,
            system="당신은 유능하고 정중한 개인 업무용 AI 비서입니다. 명확하고 깔끔하게 답변해 주세요.",
            messages=recent_messages,
        )

        ai_reply = response.content[0].text

        user_history.append({"role": "assistant", "content": ai_reply})
        history_data[user_id] = user_history
        save_history(history_data)

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

        await status_msg.edit_text(ai_reply)
        print("[이미지/문서 분석 완료]")

    except Exception as e:
        error_msg = str(e)
        print(f"[에러 발생]: {error_msg}")
        await status_msg.edit_text(f"⚠️ 파일 처리 중 오류 발생: {error_msg}")


if __name__ == "__main__":
    print("🤖 [클라우드 서버 가동] Claude 3.5 Sonnet 텔레그램 봇이 활성화되었습니다.")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    )

    app.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.ALL, handle_photo_or_document)
    )

    app.run_polling()
