# SAP ERP 加独立 WMS

下面四节和 [docs/sap-erp-wms](docs/sap-erp-wms/README.md) 里的图是同一套，图号一致。中文图直接显示在这里，英文图、PDF、SVG 用同名文件，把 `_zh` 换成 `_en`。

入库交货 `1800000001`。SAP 只认工厂 `1000` 和库存地点 `FG00`。库位和托盘在 WMS 内部。调用顺序是 SAP，然后 WMS，然后 Fleet Manager，然后车辆。Fleet Manager 这一层本实验用 openTCS。

## 图 1 接口

![图 1 接口](docs/sap-erp-wms/fig1_interfaces_zh.png)

从左到右四个系统：SAP ERP、WMS、Fleet Manager、Vehicle-01。WMS 先用 S1–S5 调用 SAP，内部上架单就绪后才用 F1–F6 调用 Fleet Manager，车辆只和 Fleet Manager 说话。图下半是这三层的接口表。库位和托盘的接口写在「WMS 内部」，不是另一个系统。

[英文](docs/sap-erp-wms/fig1_interfaces_en.png) · [PDF](docs/sap-erp-wms/fig1_interfaces_zh.pdf) · [SVG](docs/sap-erp-wms/fig1_interfaces_zh.svg)

## 图 2 时序

![图 2 时序](docs/sap-erp-wms/fig2_sequence_zh.png)

同一条链按时间往下走。② WMS 向 SAP 读未过账交货 `1800000001`。③ WMS 内部登记托盘 `4711` 并把上架单改为 STARTED，这一步不调用 Fleet Manager。④ WMS 下订单 `INB-1800000001`。⑤ 车辆只向 Fleet Manager 回报，直到 FINISHED。⑥ WMS 内部把托盘改到存储位，再调用 SAP `PostGoodsReceipt`，状态变为 `C`，物料凭证 `5000000001`，移动类型 `101`。

[英文](docs/sap-erp-wms/fig2_sequence_en.png) · [PDF](docs/sap-erp-wms/fig2_sequence_zh.pdf) · [SVG](docs/sap-erp-wms/fig2_sequence_zh.svg)

## 图 3 收货过账

![图 3 收货过账](docs/sap-erp-wms/fig3_confirm_zh.png)

图 2 第 ⑥ 步里，WMS 调用 `PostGoodsReceipt` 时 SAP 依次检查：CSRF 令牌、If-Match、ETag、交货单是否存在、状态是否仍是 `A`。有一项不通过就拒绝，交货单不变。通过后状态从 `A` 到 `C`，并写出物料凭证，移动类型 `101`。出库交货同样从 `A` 到 `C`，移动类型是 `601`。

[英文](docs/sap-erp-wms/fig3_confirm_en.png) · [PDF](docs/sap-erp-wms/fig3_confirm_zh.pdf) · [SVG](docs/sap-erp-wms/fig3_confirm_zh.svg)

## 图 4 架构

![图 4 架构](docs/sap-erp-wms/fig4_architecture_zh.png)

自上而下还是那一条链。L4 是 SAP，对象是入库交货 `1800000001`。L3 是 WMS，先调用 SAP，库位和托盘留在内部。然后调用 L2 Fleet Manager，订单 `INB-1800000001`。L1 是车辆，`Point-Park` 到 `Point-GR` 再到 `Point-Store`。下半是单号对照和状态对照：上架时 SAP 仍是 `A`，WMS 是 STARTED，Fleet Manager 是 BEING_PROCESSED。

[英文](docs/sap-erp-wms/fig4_architecture_en.png) · [PDF](docs/sap-erp-wms/fig4_architecture_zh.pdf) · [SVG](docs/sap-erp-wms/fig4_architecture_zh.svg)
