# Bonus: port grader sang Inspect AI và so khớp kết quả

Code ở `src/bonus/inspect_port.py`; test ở `src/tests/test_bonus_inspect.py`. Thư viện: `inspect-ai` 0.3.291 (`src/bonus/requirements-bonus.txt`, không nằm trong requirements của Core/CI).

```bash
cd src
python -m pip install -r bonus/requirements-bonus.txt
python -m bonus.inspect_port                                   # v1: swarm, fixed, seeded_regression
python -m bonus.inspect_port --spec config/metric_spec_v2.yaml \
  --extra sibling_refund=no_override+sibling_refund --extra misreport=no_override+misreport \
  --shapes swarm,honest_refactor,slice_regression,flaky_regression,sibling_refund,misreport \
  --out reports/bonus_inspect_compare_v2.json
```
Exit 0 = không có bất đồng nào.

## Cách port

| Khái niệm | pydantic-evals (Lab 9) | Inspect AI |
|---|---|---|
| 1 trial | `Case` (item@seed) | `Sample(id="E-001@11", input=claim_id, target=<nhãn JSON>)` |
| Chạy agent | task `run_one` của `lab9.eval` | `@solver run_shape`: gọi cùng harness Lab 8 trên SQLite mới, lưu Trial vào `state.store` |
| Grader | `Evaluator` trong `lab9.graders` | `@scorer(metrics=[accuracy()])`, trả `CORRECT`/`INCORRECT` |
| Model | MockLLM của Lab 8 | `mockllm/model` có sẵn của Inspect. Solver **không gọi** `generate()`, nên không cần API key, GPU hay mạng |

Ba grader được port:
- `approval_bypass` → `no_approval_bypass`, đảo chiều: CORRECT nghĩa là không bypass.
- `refund_amount_correct` → `refund_amount_correct`.
- `reply_matches_ledger` (FM-08) → `reply_matches_ledger`.

Các scorer được **viết lại độc lập từ quy tắc**, không import `lab9.graders`. Vì vậy hai bên khớp nhau thì đó là hai cài đặt riêng cho cùng kết luận, không phải một hàm so với chính nó. Riêng regex trích số tiền viết khác bản Lab 9.

## Kết quả (`reports/bonus_inspect_compare.json`, `reports/bonus_inspect_compare_v2.json`)

So từng (item, seed) giữa verdict của Inspect và cột tương ứng trong report `lab9.eval` cùng shape:

| Shape | Eval set | n | no_approval_bypass | refund_amount_correct | reply_matches_ledger | Bất đồng |
|---|---|---|---|---|---|---|
| swarm | v1 | 70 | 1.0000 | 0.9857 | 1.0000 | 0 |
| fixed | v1 | 70 | 1.0000 | 1.0000 | 1.0000 | 0 |
| seeded_regression | v1 | 70 | 1.0000 | 0.8571 | 0.7857 | 0 |
| swarm | v2 | 140 | 1.0000 | 0.9786 | 1.0000 | 0 |
| honest_refactor | v2 | 140 | 1.0000 | 1.0000 | 1.0000 | 0 |
| slice_regression | v2 | 140 | 1.0000 | 0.9357 | 1.0000 | 0 |
| flaky_regression | v2 | 140 | 1.0000 | 0.9643 | 1.0000 | 0 |
| fixed + `sibling_refund` | v2 | 140 | **0.6071** | 0.6071 | 1.0000 | 0 |
| fixed + `misreport` | v2 | 140 | 1.0000 | 1.0000 | **0.7857** | 0 |

Đồng thuận **100%** trên 1 050 trial × 3 grader = 3 150 verdict.

Hai dòng cuối là shape gài lỗi đăng ký tạm bằng `--extra`, chỉ trong bộ nhớ: không sửa `variants.py` hay `lab8/`. Chúng được thêm vì trên các variant công khai `approval_bypass` không bao giờ nổ. Nếu chỉ so trên các variant đó, đồng thuận 100% không chứng minh được gì ở phía lỗi.

## Nhận xét

- **Dùng Inspect khi nào:** Inspect có log `.eval` từng sample (xem bằng `inspect view`) với `explanation` của mỗi scorer. Khi debug một trial fail, cái này tiện hơn report JSON của Lab 9.
- **Inspect không thay được gì:** Inspect không có paired bootstrap, slice gate hay integrity check. Gate CI vẫn chạy trên `lab9.eval` và `lab9.gate`; Inspect chỉ là cài đặt thứ hai để kiểm chéo grader.
- **Giới hạn:**
  - Vẫn là MockLLM và cùng 28 item, nên đồng thuận 100% chỉ chứng minh hai cài đặt grader nhất quán, không chứng minh grader đúng với người dùng thật.
  - Hai bên đọc cùng một Trial (cùng ledger snapshot), nên một lỗi trong `lab8.metrics.ledger_snapshot` sẽ không bị phát hiện.
