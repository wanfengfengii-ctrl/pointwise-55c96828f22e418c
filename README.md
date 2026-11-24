# 线扫卷采录与交班

值班员用工单挂接两路设备录制，档案员在两路结清后签卷，下一班质检员停源后按正式卷号复核。程序使用 Python 3.11 标准库和 SQLite。

## 运行

```sh
python3 -B check_project.py
python3 -B demo.py
```

完整回归入口 `check_project.py` 由项目物化环境提供。演示读取 `samples/direct.json` 和 `samples/rescue.json`，数据库位于本次运行的临时目录。恢复样例的直接 A 为 `07 00 FF`，P 为 `0C FF 7F`，B 应为 `0B FF 80`。演示先核对封卷值及实际依据，再关闭现场连接并按卷号领取。

## Python 接口

用 `sqlite3.connect` 打开现场库，依次调用 `jobs.registration.initialize_jobs(connection)`、`device.replay_state.ReplayState(connection).initialize()`、`capture.custody.CustodyStore(connection).initialize()`。以另一个路径构造 `archive.storage.ArchiveStore(path)` 并调用 `initialize()`。现场接口管理自己的提交边界，调用前应结束调用者事务。重开可再次初始化，初始化保留原数据。

1. `jobs.registration.open(connection, job_id, volume_tag, a_length, b_length)` 登记并返回不可变 `JobSpec`。两带长度须为相同的正整数，单位为字节。同号异参报 `JobConflict`。
2. `device.recording.attach_recording(connection, job_id, recording)` 挂接 JSON 路径或已解析字典。相同录制可重复挂接。错卷、非法录制或试图替换录制报 `ValueError`。
3. `device.recording.read_current(connection, job_id, outlet)` 只查看当前槽。`capture.intake.step(connection, job_id, outlet, receipt=None, *, release_fault=None)` 接管当前槽，或以给定的原 `SlotReceipt` 续办。出口为 `main` 或 `backup`。结果状态为 `released`、`pending`、`ended` 或 `waiting`。`ended` 表示该出口已正常结束，`waiting` 表示没有当前槽且还未正常结束。片段或绑定不合法报 `InvalidFragment`。`device.release.query_release(connection, receipt)` 可独立查看原槽结局。回放可用 `ReleaseFault.BEFORE` 或 `ReleaseFault.AFTER` 模拟一次放槽命令失答，分别对应指令未生效和已经生效。
4. `decision.sealing.seal(connection, archive, job_id, *, before_commit=None)` 核定并签存，返回带 A、B 原字节、逐带出处和首执的 `ArchivedVolume`。未正常结束或有未核实接管时报 `RecordingUnfinished`，证据相容但欠定时报 `MissingEvidence`，矛盾时报 `ConflictingEvidence`。归档 SQLite 写入或提交失败报 `PublishFailed`。可选的临提交回调在本次新成果和首执均已写入后运行，异常会触发回滚。已签的任务重投复领原终局。
5. `archive.readback.read(archive, volume_number)` 只打开档案。卷号为 `VOL-` 加任务号，首执绑定该卷号。未签或未知卷号报 `VolumeNotAvailable`。

## 录制与档案口径

录制对象含 `recording_id`、`volume_tag` 以及 `outlets`。`outlets` 恰含 `main` 和 `backup`，每路有顺序数组 `frames` 及布尔值 `end`。每帧有 `kind`、`hex`、`crc32` 和可选的 `volume_tag`。槽序从 0 开始，两路分别编号。`end=true` 表示末槽离场后有显式正常结束事件。样例首次挂接前可编辑，挂接后输入冻结。改版录制应登记新任务。

A、B、P 均为完整二进制片。CRC32 是原字节的无符号值。错卷、错长、空载荷、未知片种或 CRC 不符的当前槽保留待处置。设备实际放槽后不再提供该片载荷，已核实的持久接管可以继续用于封卷。

P 的每个字节为同一偏移的 A 与 B 异或。两个等值 P 是两张设备收据和一条恢复关系。任何已接纳的反证都会阻止签卷。只有证据相容且唯一决定两带时才提交正式卷和首执。直接出处引用一张实际原片收据，恢复出处先引用计算所用直接片，再引用校验片。等价来源按主出口优先、出口内槽序升序选择。出处包含卷标、片种和完整的槽身份。
