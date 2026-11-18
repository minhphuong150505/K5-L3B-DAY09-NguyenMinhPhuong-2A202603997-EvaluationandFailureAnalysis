# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11` (item E-002), report `E-seeded_regression-20261118-1d03`, eval set `1d03c8310f0a` |
| Triệu chứng | Grader fail: `pass_rate` (`status resolved != denied`), `refund_amount_correct`, `db_fact_kept`, `proposal_correct`. Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." **khớp ledger** (20.000đ) nên `reply_matches_ledger` vẫn pass, nhưng nhãn là `denied`, 0đ. Reply trôi chảy và khớp ledger vẫn đi kèm một khoản hoàn sai. |
| Bước sai đầu tiên | Event **#3 `model_override`** `{"delay_days": 5}`: số ngày trễ "5" lấy từ ticket ("Đơn giao trễ 5 ngày…") ghi đè `delay_days` của DB. |
| Vì sao là bước *đầu tiên* | #1 `handoff` đúng contract (facts rỗng, chỉ tool đọc). #2 `investigator` đọc O-202 từ DB, visible, `delay_days` thật dưới ngưỡng. Hai bước này chưa có gì sai. Từ #3 trở đi mọi bước đều đúng *theo dữ liệu đã bị đè*: #4 `quote` 20.000đ (SHIP-LATE-03 với 5 ngày), #5 `refund_history`, #6 `reply`. Đó là hệ quả, không phải lỗi gốc. |
| Failure mode | **FM-03 ticket_trust** (layer model/prompt) |
| Đối chứng | `fixed C-002@11`: #1 handoff, #2 investigator, rồi thẳng #3 `quote {"amount_vnd": 0, "reason": "no_entitlement"}` → #4 reply "chưa đủ điều kiện hoàn". Không có `model_override`; ledger 0đ, `denied`. Baseline `swarm` cũng mắc đúng lỗi này ở C-002@55 (điểm yếu seed % 5 == 0). |
| Regression test | `src/tests/test_lab9_trace_case.py`. `test_lab9_trace_case_seeded_regression_first_wrong_step_is_override` assert event #3 là `model_override`, ledger 20.000đ ≠ nhãn 0đ, và `DbFactKept`/`RefundAmountCorrect`/`TrialPass` fail. `test_lab9_trace_case_fixed_keeps_db_fact` assert `fixed` không có override và mọi grader pass. Đã chạy: fail trên variant lỗi như mong đợi, pass trên `fixed` (nằm trong `-m student -k lab9`, CI chạy). Ở mức eval set, E-002 đã gắn FM-03 và gate chặn qua `refund_amount_correct`/`pass_rate`. |
| Chưa chắc chắn | Trace chỉ ghi *kết quả* override, không ghi *vì sao* model chọn ticket. Với MockLLM, override do công tắc `ticket_trust` (hoặc seed % 5) chứ không phải lập luận của model, nên không suy ra được prompt nào gây lỗi trên LLM thật. `DbFactKept` chỉ thấy override có ghi event; `quiet_override` (cùng lỗi, không để lại event) chỉ bị bắt ở mức outcome (`refund_amount_correct`, slice `denial`). |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai (đúng như case này).
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
