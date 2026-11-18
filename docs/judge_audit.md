# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (FAIL = lớp dương: việc của judge là tìm lỗi).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **PASS** (exit 0) | dùng (chỉ cho tone, không cho tiền) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **FAIL** (exit 1): κ và R dưới sàn | loại |

Ma trận nhầm lẫn:
- tone_ok: tp 24, fp 4, fn 6, tn 66.
- helpful: tp 9, fp 0, fn 21, tn 70.

Hiệu chỉnh Rogan-Gladen:
- tone: tỉ lệ fail quan sát 0.28 → hiệu chỉnh 0.30, judge gần đúng.
- helpful: tỉ lệ fail quan sát 0.09 → hiệu chỉnh 0.30. Judge báo tỉ lệ lỗi **thấp hơn 3 lần** thực tế.

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha, anh check giúp em." | PASS | FAIL | Người | Xưng "em" và "nha" quá suồng sã. Rubric thêm: *xưng "LogiX" hoặc "anh/chị"; FAIL nếu xưng "em/bạn" hoặc dùng tiểu từ "nha/nhé"*. |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ…" | PASS | FAIL | Người | Cùng lỗi xưng hô ("bạn"). Thêm 2 ví dụ FAIL có mở đầu bằng "Dạ" để judge không coi "Dạ" là đủ lịch sự. |
| 3 | H-029 "Dạ, vui lòng chờ… không cần nhắn thêm." | PASS | FAIL | Người | Giọng ra lệnh. Rubric thêm: *FAIL nếu có câu mệnh lệnh hoặc cấm khách ("không cần…", "đừng…")*. Không chấm theo từ khóa lịch sự. |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì "thiếu xin lỗi". Rubric ghi rõ: *xin lỗi **không bắt buộc** khi yêu cầu đã được giải quyết; ngắn mà đủ ý vẫn PASS*. |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ…" | FAIL | PASS | Người | Style guide CS LogiX cho phép từ "refund". Thêm danh sách từ tiếng Anh được phép (refund, ship, voucher). |
| 6 | HL-015 "Dạ, LogiX đã hoàn 55.000đ cho đơn O-112 ạ." (ledger 40.000đ) | PASS | FAIL | Người | Judge không thấy ledger nên không thể biết số tiền sai. **Không sửa rubric được.** Kiểm tra này chuyển sang grader code `ReplyMatchesLedger` (FM-08). |
| 7 | HL-016 xin lỗi dài, "hoàn 55.000đ" (ledger 35.000đ) | PASS | FAIL | Người | Judge bị lời xin lỗi dài làm lóa. Ngoài lỗi số tiền, rubric helpful cần phạt câu lặp ý và độ dài vô ích. |
| 8 | HL-020 "hoàn 90.000đ" (ledger 120.000đ) | PASS | FAIL | Người | Như #6: lỗi về tiền, chỉ code có ledger mới chấm được. |

Quy luật: 21/21 false negative của helpful đều là reply nói sai tiền. Judge chỉ đọc chữ, không có ledger, nên recall chỉ 0.30.

## Kết luận
- **tone_ok dùng được**, nhưng chỉ làm metric theo dõi (warn), không dùng để chặn tiền. Lý do: κ 0.757, P 0.857, R 0.800 đều trên sàn. 6 FN đều là lỗi xưng hô/ra lệnh mà rubric chưa ghi; sửa rubric theo #1–#5 rồi calibrate lại trên nhãn mới.
- **helpful bị loại khỏi gate.**
  - Recall 0.30: bỏ sót 21/30 reply hỏng. κ 0.375 < 0.6.
  - P = 1.0 vô nghĩa khi judge gần như luôn nói PASS.
  - Lỗi nó bỏ sót là lỗi sai tiền, một tiêu chí theo quy tắc phải chấm bằng code. Vì vậy phần "đúng tiền" của helpful đã thành `reply_matches_ledger` (FM-08); judge không được dùng cho tiền.
- Rủi ro còn lại:
  - Judge chỉ được kiểm trên dữ liệu replay đã công khai (n=100). Sửa rubric theo chính tập này sẽ overfit; cần nhãn người mới để có held-out thật.
  - Chưa đo độ ổn định của judge khi đổi model hoặc prompt.
