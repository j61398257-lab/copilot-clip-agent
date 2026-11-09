# SAP 到车队的任务管理学习环境

这条环境用来看清一条串行调用：SAP ERP，然后 WMS，然后 Fleet Manager，然后车辆，最后由 WMS 把收货过账写回 SAP。车辆不会真的动。Fleet Manager 这一层本实验用 openTCS，Loopback 驱动会按路线走完并回报。SAP 管物料、工厂、库存地点、交货单和物料凭证。仓位、托盘和上架单在 WMS 内部。不走 EWM 仓库任务。

仓库里有两套链路，由下面这一条命令一起启动。完整版用 SAP ERP 的 OData 模拟、直接调用 SAP 的 WMS，以及 Fleet Manager。简化版在 `python/` 目录，用 Python 标准库写成四个小服务，放在同一个容器里跑。中间没有桥接。架构和入库交货 `1800000001` 的逐步对应见 [架构.md](架构.md)。接口见 [SAP标准接口.md](SAP标准接口.md)。

`docs/figures/` 里有五张图，每张都有中英文两版的 PDF、SVG 和 600 dpi PNG，由 `make_figures.py` 生成：

| 图 | 内容 | 在哪篇文档里讲 |
| --- | --- | --- |
| `fig1_interfaces` | 系统与接口总览 | [SAP标准接口.md](SAP标准接口.md) |
| `fig2_sequence` | 入库交货 `1800000001` 的调用时序 | [SAP标准接口.md](SAP标准接口.md) |
| `fig3_confirm` | 收货过账的校验链，以及交货单状态 | [SAP标准接口.md](SAP标准接口.md) |
| `fig4_architecture` | 分层架构原理、单号和状态的换算 | [架构.md](架构.md) |
| `fig5_run_state` | 一次真实运行中各系统状态随时间的变化。改成 ERP 之后还没有新的采样，这张图仍是原先 EWM 那次运行，不要拿它对照现在的调度板 | [架构.md](架构.md) |

## 运行

只需要 Docker Desktop，不用装 Java、Node 或 Python。代码在这个仓库的 `sap-wms-fleet` 目录，下载本仓库即可，不用再去 `sap-wms-fleet-lab`。

1. 安装并启动 Docker Desktop。电脑要是 64 位 x86，ARM 版 Windows 不行，OpenWMS 镜像只有 amd64。内存 8 GB 够用，磁盘留 5 GB 以上。
2. 下载代码，不用登录 GitHub。在 PowerShell 里执行：

   ```powershell
   curl.exe -L -o copilot-clip-agent.zip https://github.com/j61398257-lab/copilot-clip-agent/archive/refs/heads/master.zip
   tar -xf copilot-clip-agent.zip
   cd copilot-clip-agent-master\sap-wms-fleet
   ```

   macOS 或 Linux 把最后一行的反斜杠换成正斜杠。也可以在仓库页面点 **Code → Download ZIP**，解压后进入 `copilot-clip-agent-master/sap-wms-fleet`。
3. 启动全部容器，包含完整版和 Python 简化版：

   ```powershell
   docker compose up --build -d
   ```

   第一次要下载镜像并构建，视网速大约 5 到 10 分钟，其中最慢的是从 GitHub 下载 87 MB 的 openTCS。以后再启动只要几十秒。
4. 打开 <http://localhost:8080>。这一页是架构图，每个框里的文字是该服务此刻的真实结果，不是画上去的示例。两条链路的入库交货 `1800000001` 都显示「已收货」，托盘在存储位 `FGIN/ST/0001/0000/0000`，库区平面上两块牌子都停在存储位，才算跑通。完整版大约一分钟，Python 大约 5 秒，页面每 2 秒自己刷新。出库交货 `8000000001` 留在 SAP 里，状态仍是未过账，这条自动链路先跑入库。

停掉用 `docker compose down`。数据都在内存里，再次 `docker compose up -d` 会从预制的入库交货重新跑。

## 只跑通一条链

完整版要访问 Docker Hub（基础镜像和 OpenWMS）和 GitHub（openTCS）。只想先看 SAP、WMS、Fleet Manager、车辆这条串行链，在 `sap-wms-fleet` 目录执行：

```powershell
cd python
docker compose up --build -d
```

这一条只构建一个容器，基础镜像是 `python:3.12-slim`，不下载 OpenWMS，也不下载 openTCS。几秒后打开 <http://localhost:9120/status>。日志里出现「入库交货 1800000001 上架完成，收货已回写 SAP」即这条链跑通：

```powershell
docker compose logs
```

回到上一级跑完整版之前，先在 `python` 目录执行 `docker compose down`，否则 `9004`、`9120`、`9200`、`9300` 会被占用。

## 再下一笔入库

完整版的 SAP 模拟的是 S/4HANA ERP 的入库交货和收货过账，不是 EWM。新建一张入库交货要先取 CSRF 令牌：

```powershell
$svc = 'http://localhost:4004/sap/opu/odata/sap/API_INBOUND_DELIVERY_SRV;v=0002'
$token = (Invoke-WebRequest -Method Head -Uri "$svc/" -Headers @{ 'X-CSRF-Token' = 'Fetch' } -UseBasicParsing).Headers['x-csrf-token']
Invoke-RestMethod -Method Post -Uri "$svc/A_InbDeliveryHeader" -ContentType 'application/json' -Headers @{ 'X-CSRF-Token' = $token } -Body '{"Supplier":"V200","to_DeliveryDocumentItem":{"results":[{"Material":"MAT-100","Plant":"1000","StorageLocation":"FG00","ActualDeliveryQuantity":2,"DeliveryQuantityUnit":"EA","PurchaseOrder":"4500000002","PurchaseOrderItem":"00010"}]}}'
```

WMS 只接手货物移动状态为 `A` 的入库交货。上架完成后它调用 `PostGoodsReceipt`，交货单变成 `C`，并产生移动类型 `101` 的物料凭证。字段和错误见 [SAP标准接口.md](SAP标准接口.md)。

## 链路

1. SAP 里有一张未过账的入库交货 `1800000001`，物料 `MAT-100`，工厂 `1000`，库存地点 `FG00`。SAP 不知道仓位。
2. WMS 先读物料主数据和库存地点，在收货位登记托盘，再创建到存储位的上架运输单。
3. WMS 内部上架单就绪后，才向 Fleet Manager 下一张运输单：到收货位装货，再到存储位卸货。
4. Loopback 车辆跑完后，WMS 把运输单改为完成，并调用 `PostGoodsReceipt`。SAP 生成物料凭证，交货单变为 `C`。

执行期间交货单一直是 `A`。交货单对应哪张 WMS 运输单、哪张 Fleet Manager 订单、哪张物料凭证，记在 WMS 里，<http://localhost:8090/status> 的 `links` 能看到。

状态对应关系：

| SAP 入库交货 | WMS | Fleet Manager |
| --- | --- | --- |
| 未过账 `A` | 创建运输单，STARTED | BEING_PROCESSED |
| 已过账 `C` | FINISHED | FINISHED |
| 仍是 `A`，WMS 停手 | ONFAILURE | FAILED 或 UNROUTABLE |

工厂 `1000`、库存地点 `FG00` 对应两套 WMS 库位：收货 `FGIN/GR/0001/0000/0000`，存储 `FGIN/ST/0001/0000/0000`。出库交货 `8000000001` 和 `PostGoodsIssue`（移动类型 `601`）已经在 SAP 模拟里，WMS 这一版不自动拣货。

`common-compat` 夹在 OpenWMS 的两个服务之间。TMS 2.0 查库位组时只认单个对象，Common 3.0 不带名字时返回数组，它把数组取成一个对象。

## Python 简化版

`python/` 目录用 Python 标准库把同一条链路写成四个小服务，只演示正常完成。上面的 `docker compose up --build -d` 已经把它放进 `python-chain` 容器，不要再在本机执行 `python -m chain`，端口会冲突。接口是简化过的，路径和字段不等于真实的 OData、OpenWMS、openTCS 接口。

容器日志里按时间打印每个系统的状态变化：

```powershell
docker compose logs python-chain
```

大约 5 秒后出现「入库交货 1800000001 上架完成，收货已回写 SAP」。运行中可以在浏览器打开：

| 系统 | 地址 |
| --- | --- |
| SAP 入库交货 | <http://localhost:9004/inbound-deliveries> |
| SAP 物料凭证 | <http://localhost:9004/material-documents> |
| WMS 运输单 | <http://localhost:9120/transport-orders> |
| Fleet Manager 运输订单 | <http://localhost:9200/transport-orders> |
| 车辆 | <http://localhost:9300/state> |
| WMS 调用 SAP 的进度 | <http://localhost:9120/status> |

和完整版的对应关系：

| 完整版 | 简化版 |
| --- | --- |
| SAP ERP 的 OData V2：`API_PRODUCT_SRV`、`API_INBOUND_DELIVERY_SRV`、`API_OUTBOUND_DELIVERY_SRV`、`API_MATERIAL_DOCUMENT_SRV` | `chain/sap.py`：同一批业务对象的简化 JSON。过账要带 If-Match，版本不对返回 412，已经过账返回 409 |
| `wms/index.js` 先调用 SAP，库位和上架单留在 WMS 内部 | `chain/wms.py`：轮询未过账的入库交货，在内部登记托盘并上架，然后再调用 Fleet Manager，完成后回写收货 |
| Fleet Manager，本实验用 openTCS | `chain/fleet.py`：运输订单分配给空闲车辆，按路线拆成一段段行驶任务下发 |
| 车辆 | `chain/vehicle.py`：沿路线每走一步、每做完一个动作就向 Fleet Manager 回报 |

不用 Docker、直接在本机跑 Python 版（需要 Python 3.10 以上，只用标准库）：

```powershell
docker compose down
cd python
python -m chain
```

这和根目录那条 Docker 命令二选一，容器开着时端口会冲突，所以先 `docker compose down`。几秒后日志里出现「入库交货 1800000001 上架完成，收货已回写 SAP」，按 Ctrl+C 停止。测试在 `python` 目录里执行 `python -m unittest -b`，它用临时端口，容器开着也能跑。SAP 模拟的测试在 `sap` 目录执行 `node --test server.test.js`。

## 重新生成图

`docs/figures` 里的图已经生成好，直接打开就能看。改了代码想重新出图时，需要 Python 3.10 以上和 matplotlib：

```powershell
pip install matplotlib
docker compose down
python docs/figures/record_run.py
cd docs/figures
python make_figures.py
```

`record_run.py` 会自己执行 `docker compose up -d`，两条链路的入库交货都过账后再多记 3 秒，把数据写进 `run_trace.json`。`make_figures.py` 读这份数据生成图 5；采样文件还是旧架构时会跳过图 5，避免把 EWM 的曲线画成入库交货。只改了图 1 到图 4 的文字时，直接在 `docs/figures` 执行 `python make_figures.py`。

## 注意

- SAP 模拟是仓库里的 `sap/server.js`，不包含 SAP 的软件，可以和另外 7 个镜像一起发布。
- 第一次构建要访问 Docker Hub 和 GitHub（下载 openTCS 7.5.0）。拉镜像卡住时，在 Docker Desktop 的设置里配置代理或镜像加速。只验证串行链时，用上面的 Python 容器，它不拉 OpenWMS 和 openTCS。
- 端口 `4004`、`8080`、`8090`、`8120`、`8131`、`55200`、`9004`、`9120`、`9200`、`9300` 不能被别的程序占用。调度板是 `8080`。`8090` 是完整版 WMS 调用 SAP 的进度，`9120` 是 Python 版 WMS。
- Docker 的虚拟磁盘默认在 C 盘。C 盘空间不多时，先在 Docker Desktop 的 **Settings → Resources → Advanced** 里把磁盘位置改到 D 盘。
