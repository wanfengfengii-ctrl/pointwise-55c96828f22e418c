# 双卷合载采录

值班员可给两个已登记且未挂接的工单装一只主路载具。载具恰有两成员，每个工单各占一员。各工单的普通备用路仍独立采录。共享槽和普通槽用各自的放行入口。初始化、备用路接管、封卷和断源领取仍按 README.md 调用。

## 现场调用

- `device.recording.attach_shared_slot(connection, slot_id, members)` 挂接载具。`slot_id` 是现场库内标识该载具的一段非空文本。`members` 为两成员列表，每员有 `job_id`、`recording_id`、`main` 和 `backup`。`main` 是 README.md 的单帧对象，`backup` 使用其中一路的 `frames` 和 `end` 结构。未写帧卷标时用对应工单卷标。两卷挂接共同生效，槽号已用于其他挂接或工单已有录制时报 `ValueError`。合载工单保持原绑定。
- `device.recording.read_shared_member(connection, slot_id, job_id)` 读指定成员。没有可读成员时返回 `None`。读到时返回字典，含 `slot_id`、`job_id`、`recording_id`、`volume_tag`、`kind`、`payload`（`bytes`）、`checksum`（无符号 CRC32）、`write_id` 和 `proof`。`write_id` 是该成员实际写入的不透明字符串标识。`proof` 是设备供查证本次读件的凭据，调用方按原样交回。两字段的内部组织由实现决定。
- `device.recording.rewrite_shared_member(connection, packet, frame)` 凭仍有效的成员回包改写该成员。`frame` 沿用帧对象格式。成员已实收、凭据过时或归属不符时报 `ValueError`。每次成功改写产生新的实际写入身份，即使新旧字节相同。其他成员的实读资格不受影响。用读入口取得新回包。
- `capture.intake.accept_shared_member(connection, job_id, packet, *, before_commit=None)` 实收原回包，返回持久实收结果。结果为字典，至少含 `slot_id`、`job_id`、`recording_id` 和 `write_id`。普通片验仍适用。归属、正文或凭据不符时报 `capture.validation.InvalidFragment`。已实收成员的原回包可再次领取等值结果，即使载具已经放行。可选回调在新实收写入后、提交前调用。回调异常原样传出并回滚本次实收。普通主路 `step` 不可接纳合载成员，普通 `release_slot` 不可释放合载槽。
- `device.release.query_shared_release(connection, slot_id)` 返回 `current`、`released` 或 `unknown`。`device.release.release_shared_slot(connection, slot_id)` 对已知槽请求放行。两成员持久实收前返回 `current` 并保留槽。放行后返回 `released`，重查和重投可取得同一结局。未知槽放行时报 `ValueError`。

## 签卷与查源

沿用 `decision.sealing.seal`。合载成员的持久实收使本卷主路结清，普通备用路须按原规则结清。已齐备的卷可在另一卷待收且载具仍占用时签出。缺片、矛盾及归档失败沿用原规则。按 `archive.readback.read` 离线领取时，采用的合载来源在 `EvidenceSource.to_data()` 的原字段之外带 `slot_id` 和 `write_id`。`job_id` 与 `recording_id` 标识实际成员，`outlet` 为 `main`。旧卷可按原方式读取。

## 现场样例

`samples/shared_slot.json` 给出载具、工单、备用片和两种 Y 更正。两份备用 P 均为 0C。先取 X=07、Y=09 的回包，再把 Y 改为 0A。旧 X 可实收并签出 B=0B，此时载具仍占用。旧 Y 被拒。Y=0A 持久实收后可放槽并签出 B=06。把 Y 等值改写为 09 时，旧回包仍失效，新回包签出 B=05。

`python3 -B shared_scenario.py fixture` 校核样例。完成合载后可用 `python3 -B shared_scenario.py verify` 核验两种更正和失败续交，用 `python3 -B shared_scenario.py demo` 展示实际断源领取结果。把这些业务核验与演示接入现有 `.pointwise-runtime.json`，同时保留 `check_project.py` 的完整原回归及失败退出状态。清单沿用既有 version、kind、setup、verify 和 demo 格式。
