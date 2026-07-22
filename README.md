# PhoneBot FA Console — Platform Chẩn Đoán Lỗi Trạm Test Tự Động

PhoneBot Failure Analysis (FA) Console là một nền tảng chuyên dụng, ngoại tuyến hoặc đám mây (hybrid offline-first) dùng để chẩn đoán, phân loại lỗi phần cứng và phần mềm của các thiết bị di động trên trạm test tự động.

Hệ thống kết hợp phân tích bộ quy tắc xác định (Deterministic Pre-scan) với mô hình AI tiên tiến (Ollama, OpenAI, Gemini) nhằm tối ưu độ chính xác và bảo mật dữ liệu.

---

## 🚀 Các Tính Năng Vừa Nâng Cấp Nổi Bật (Mới Nhất)

### 1. Nút Chuyển Đổi Giao Diện Sáng / Tối (Light & Dark Mode Switcher)
* **Chuyển đổi mượt mà không lag**: Thiết kế nút chuyển đổi trực quan, sang trọng tích hợp gọn gàng trong mục **Cài đặt & Giao diện** với các hiệu ứng chuyển tiếp (transitions 0.25s) êm dịu, không gây giật lag phần cứng.
* **Tương phản văn bản hoàn hảo**: Giao diện sáng (Light Mode) được tinh chỉnh màu sắc thủ công cực kỳ cao cấp, sử dụng hệ màu tương phản chất lượng cao (`#1e293b`, `#475569`) cho các bảng biểu, nhãn nhập liệu, nhật ký log, và khung chat AI để đảm bảo dễ đọc và không mỏi mắt.
* **Không nháy màn hình (Anti-FOUC)**: Áp dụng khối mã tự khởi chạy đồng bộ ngay khi tải trang giúp giữ nguyên trạng thái giao diện đã chọn từ `localStorage` trước khi render DOM, triệt tiêu hoàn toàn lỗi nháy giao diện khi tải lại trang (F5).

### 2. Bộ Công Cụ Tính UPH Tích Hợp (PB UPH Calculator V6)
* **Tích hợp đồng bộ trong Console**: Tách biệt luồng tính toán UPH riêng trên Sidebar ("Tính UPH") với giao diện lưới hiển thị đồng bộ cùng màu sắc của hệ thống.
* **Tự động phân tích lịch sử giao dịch (Tx Logs)**: Chỉ cần dán nhật ký giao dịch, hệ thống tự động bóc tách thời gian bắt đầu, kết thúc, thời lượng chạy, các khoảng thời gian trống (Idle gaps) và phân loại thiết bị lỗi tự động.
* **Xuất báo cáo Excel offline**: Hỗ trợ xuất trực tiếp bảng kết quả UPH ra file định dạng Excel (`.xlsx`) hoàn toàn bằng thư viện nén dữ liệu cục bộ phía Client, không cần gửi dữ liệu lên server.
* **Tự động lưu trạng thái**: Mọi dữ liệu đang nhập và kết quả tính toán được tự động đồng bộ xuống trình duyệt để tiếp tục làm việc sau khi tải lại trang.

### 3. Phân Tích Ngữ Nghĩa Bằng Vector (Semantic Vector Search)
* **Offline-first Vector Engine**: Lưu trữ trực tiếp các vector nhúng (Embeddings) của logs lỗi vào SQLite mà không cần cài đặt các cơ sở dữ liệu vector cồng kềnh bên ngoài.
* **Hỗ trợ đa Provider**: Tương thích tốt với **Ollama** (`nomic-embed-text`), **Gemini API** (`text-embedding-004`) và **OpenAI API** (`text-embedding-3-small`).
* **Trọng số Hybrid Scoring**: Thuật toán tìm kiếm kết hợp thông minh (80% dựa trên độ tương đồng Cosine ngữ nghĩa từ AI + 20% từ các đặc trưng deterministic thực tế như trùng mã lỗi linh kiện) cho độ chính xác tìm kiếm case cũ vượt mức 95%.
* **Hiển thị Case tương tự Glassmorphic**:
  * Điểm số khớp hiển thị dạng Vòng tròn sắc màu động (Xanh: Khớp cao, Vàng: Khớp vừa, Đỏ: Khớp thấp).
  * Gắn nhãn phân loại lý do khớp thông minh: Màu Cyan cho khớp ngữ nghĩa AI (`semantic-badge`), Màu Tím cho trùng mã lỗi phần cứng (`code-match-badge`).

### 4. Trợ Lý Trực Tuyến AI Copilot (Interactive Chatbot)
* **Trò chuyện trực tiếp trên Case**: Tab **AI Copilot 💬** mới trong panel kết quả cho phép kỹ sư trò chuyện trực tiếp với AI về case lỗi đang xem.
* **Truyền Ngữ cảnh Thông minh**: Hệ thống tự động nạp tóm tắt lỗi, kết luận, cùng với **toàn bộ dữ liệu logs trích dẫn** của case vào ngữ cảnh trò chuyện của LLM.
* **Bộ biên dịch Markdown thời gian thực**: Định dạng câu trả lời chứa danh sách đầu dòng, mã log, hay tô đậm chữ kỹ thuật thành giao diện HTML đẹp mắt và trực quan.

### 5. Sửa Lỗi Đồng Bộ Time Domain & Cô Lập Port
* **Timestamp Domain Alignment**: Tự động liên kết các mốc thời gian lệch pha giữa Android `trace.log`, Station log và OCR/Vision.
* **Cô lập Port chính xác**: Loại bỏ log nền nhiễu (polling, heartbeat, log chờ thiết bị khác) trên trạm test 4-port, giúp hiển thị thời gian chạy thực tế (Runtime) của từng thiết bị cực kỳ chính xác.

---

## 🛠️ Yêu Cầu Hệ Thống & Khởi Động

### 📦 Các thư viện yêu cầu (Dependencies)
* Python 3.10+
* FastAPI, Uvicorn
* OpenAI SDK (cho cả OpenAI và Ollama)
* Google GenAI SDK (cho Gemini)
* SQLite3

### 💻 Chạy Trực Tiếp Trên Ubuntu / Linux

1. Thiết lập quyền và khởi động ứng dụng:
   ```bash
   chmod +x start_ubuntu.sh
   ./start_ubuntu.sh
   ```
   *Script sẽ tự động khởi tạo môi trường ảo `.venv`, cài đặt các thư viện cần thiết và chạy server.*

2. Truy cập giao diện Web qua trình duyệt:
   ```
   http://127.0.0.1:8000
   ```

### 🪟 Chạy Trên Windows

* Nhấp đúp chuột vào file `start_windows.bat` để tự động chạy môi trường Python và mở trình duyệt.

---

## ⚙️ Cấu Hình Mô Hình Phân Tích & Embeddings

Thiết lập biến môi trường trong file `.env` hoặc nhập trực tiếp tại thanh **Cài đặt nâng cao** trên giao diện:

| Tham số | Ý nghĩa | Lựa chọn / Giá trị ví dụ |
|---|---|---|
| `phonebot.provider` | Provider được sử dụng | `ollama` / `openai` / `gemini` |
| `phonebot.model` | Model phân tích | `qwen2.5-coder:7b` / `gemini-2.5-flash` / `gpt-4o-mini` |
| `phonebot.baseUrl` | Base URL của Ollama (nếu dùng) | `http://localhost:11434/v1` |
| `nomic-embed-text` | Model vector nhúng của Ollama | Cài đặt thông qua lệnh: `ollama pull nomic-embed-text` |

> [!TIP]
> Để sử dụng tìm kiếm ngữ nghĩa offline hoàn toàn bằng Ollama, hãy mở Terminal trên máy trạm test và chạy:
> ```bash
> ollama pull qwen2.5-coder:7b
> ollama pull nomic-embed-text
> ```

---

## 🗄️ Quản Lý Cơ Sở Dữ Liệu (Database)

* Cơ sở dữ liệu được lưu trữ tại file cục bộ: `data/phonebot_cases.db`.
* **Giữ database cũ**: Bạn chỉ cần sao chép file `phonebot_cases.db` của phiên bản cũ đè vào thư mục `data/` trước khi khởi động ứng dụng. Hệ thống sẽ tự động cập nhật cấu trúc bảng (Migration) để bổ sung trường vector `embedding_json` mà không làm mất bất kỳ dữ liệu case lịch sử nào của bạn.
