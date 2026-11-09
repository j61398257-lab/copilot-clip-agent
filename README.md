# SAP ERP 加独立 WMS

容器在 [sap-wms-fleet](sap-wms-fleet/README.md)。另一台电脑装好 Docker Desktop，下载这个仓库就能构建并跑起来。下面四节图和 [docs/sap-erp-wms](docs/sap-erp-wms/README.md) 是同一套，页面上直接显示英文图。中文图把文件名里的 `_en` 换成 `_zh`。

## 下载镜像

8 个镜像打成一个文件，放在本仓库的 Release。不用 Docker Hub，不用登录。点这个链接直接下载（约 633 MB）：

https://github.com/j61398257-lab/copilot-clip-agent/releases/download/sap-wms-images/sap-wms-fleet-images.tar.gz

校验码 `814dd8e49e0e5eb8c2ac0bf824e64b6d04bebe6aa37e12ab562c477940097d61`。发布页在 https://github.com/j61398257-lab/copilot-clip-agent/releases/tag/sap-wms-images 。

这是现在这一版：里面有 `wms`，没有 `bridge`。`sap-wms-fleet-lab` 仓库 11 月 8 日的 `v1.0.0` 是上一版，不要用那一份。

PowerShell：

```powershell
curl.exe -L -o copilot-clip-agent.zip https://github.com/j61398257-lab/copilot-clip-agent/archive/refs/heads/master.zip
tar -xf copilot-clip-agent.zip
cd copilot-clip-agent-master\sap-wms-fleet
curl.exe -L -o sap-wms-fleet-images.tar.gz https://github.com/j61398257-lab/copilot-clip-agent/releases/download/sap-wms-images/sap-wms-fleet-images.tar.gz
docker load -i sap-wms-fleet-images.tar.gz
docker compose -f compose.yaml -f compose.ghcr.yaml up -d
```

macOS 或 Linux 把 `cd` 那一行的反斜杠换成正斜杠。也可以在本页点 **Code → Download ZIP**，解压后进入 `copilot-clip-agent-master/sap-wms-fleet`，再下载镜像包并 `docker load`。

打开 <http://localhost:8080>。完整版大约一分钟，Python 简化版大约 5 秒。两条入库交货 `1800000001` 都显示已收货，托盘在存储位 `FGIN/ST/0001/0000/0000`，才算跑通。停掉用 `docker compose -f compose.yaml -f compose.ghcr.yaml down`。

电脑要是 64 位 x86，内存 8 GB，磁盘留 5 GB。ARM 版 Windows 跑不了完整版，OpenWMS 镜像只有 amd64。镜像已经在包里，加载时不再访问 Docker Hub。

没有这个镜像包、要从源码构建时，在 `sap-wms-fleet` 目录执行 `docker compose up --build -d`。那一次要能访问 Docker Hub 和 GitHub。

只想先跑通 SAP → WMS → Fleet Manager → 车辆这一条，可以只起 Python 那一个容器，不下载 OpenWMS 和 openTCS：

```powershell
cd python
docker compose up --build -d
docker compose logs
```

日志里出现「入库交货 1800000001 上架完成，收货已回写 SAP」，并打开 <http://localhost:9120/status>，这条链就算跑通。再跑完整版之前，先在 `python` 目录执行 `docker compose down`。

逐步说明、端口和接口见 [sap-wms-fleet/README.md](sap-wms-fleet/README.md)。

入库交货 `1800000001`。SAP 只认工厂 `1000` 和库存地点 `FG00`。库位和托盘在 WMS 内部。调用顺序是 SAP，然后 WMS，然后 Fleet Manager，然后车辆。Fleet Manager 这一层本实验用 openTCS。

## 图 1 接口

![Fig. 1 Interfaces](docs/sap-erp-wms/fig1_interfaces_en.png)

从左到右四个系统：SAP ERP、WMS、Fleet Manager、Vehicle-01。WMS 先用 S1–S5 调用 SAP，内部上架单就绪后才用 F1–F6 调用 Fleet Manager，车辆只和 Fleet Manager 说话。图下半是这三层的接口表。库位和托盘的接口写在 WMS 内部，不是另一个系统。

[PDF](docs/sap-erp-wms/fig1_interfaces_en.pdf) · [SVG](docs/sap-erp-wms/fig1_interfaces_en.svg) · [中文图](docs/sap-erp-wms/fig1_interfaces_zh.png)

## 图 2 时序

![Fig. 2 Sequence](docs/sap-erp-wms/fig2_sequence_en.png)

同一条链按时间往下走。② WMS 向 SAP 读未过账交货 `1800000001`。③ WMS 内部登记托盘 `4711` 并把上架单改为 STARTED，这一步不调用 Fleet Manager。④ WMS 下订单 `INB-1800000001`。⑤ 车辆只向 Fleet Manager 回报，直到 FINISHED。⑥ WMS 内部把托盘改到存储位，再调用 SAP `PostGoodsReceipt`，状态变为 `C`，物料凭证 `5000000001`，移动类型 `101`。

[PDF](docs/sap-erp-wms/fig2_sequence_en.pdf) · [SVG](docs/sap-erp-wms/fig2_sequence_en.svg) · [中文图](docs/sap-erp-wms/fig2_sequence_zh.png)

## 图 3 收货过账

![Fig. 3 Goods receipt](docs/sap-erp-wms/fig3_confirm_en.png)

图 2 第 ⑥ 步里，WMS 调用 `PostGoodsReceipt` 时 SAP 依次检查：CSRF 令牌、If-Match、ETag、交货单是否存在、状态是否仍是 `A`。有一项不通过就拒绝，交货单不变。通过后状态从 `A` 到 `C`，并写出物料凭证，移动类型 `101`。出库交货同样从 `A` 到 `C`，移动类型是 `601`。

[PDF](docs/sap-erp-wms/fig3_confirm_en.pdf) · [SVG](docs/sap-erp-wms/fig3_confirm_en.svg) · [中文图](docs/sap-erp-wms/fig3_confirm_zh.png)

## 图 4 架构

![Fig. 4 Architecture](docs/sap-erp-wms/fig4_architecture_en.png)

自上而下还是那一条链。L4 是 SAP，对象是入库交货 `1800000001`。L3 是 WMS，先调用 SAP，库位和托盘留在内部。然后调用 L2 Fleet Manager，订单 `INB-1800000001`。L1 是车辆，`Point-Park` 到 `Point-GR` 再到 `Point-Store`。下半是单号对照和状态对照：上架时 SAP 仍是 `A`，WMS 是 STARTED，Fleet Manager 是 BEING_PROCESSED。

[PDF](docs/sap-erp-wms/fig4_architecture_en.pdf) · [SVG](docs/sap-erp-wms/fig4_architecture_en.svg) · [中文图](docs/sap-erp-wms/fig4_architecture_zh.png)
