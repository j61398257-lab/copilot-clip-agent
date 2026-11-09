# SAP ERP 加独立 WMS

没有桥接。WMS 直接调用 SAP。

入库交货 `1800000001`：SAP 只认工厂 `1000` 和库存地点 `FG00`。库位、托盘和上架在 WMS。WMS 向车队派车，车从收货位送到存储位之后，WMS 再调用 SAP 过账收货，移动类型 `101`，物料凭证 `5000000001`。

往下拉过文件列表就能看到图。这些图直接画在页面上。

## 调用方向

```mermaid
flowchart TB
  SAP["SAP S/4HANA ERP · :4004<br/>工厂 1000 · 库存地点 FG00<br/>不管仓位"]
  WMS["WMS · OpenWMS<br/>直接调用 SAP<br/>收货位 FGIN/GR/0001/0000/0000<br/>存储位 FGIN/ST/0001/0000/0000"]
  TCS["openTCS · :55200<br/>订单 INB-1800000001<br/>GoodsReceipt 装货，Storage-01 卸货"]
  CAR["Vehicle-01<br/>Point-GR 到 Point-Store"]
  WMS -->|"S2 读取未过账入库交货"| SAP
  WMS -->|"S5 上架完成后 PostGoodsReceipt"| SAP
  WMS -->|"F4 派上架"| TCS
  TCS --> CAR
  style SAP fill:#d6e6f5,stroke:#0072B2,color:#222
  style WMS fill:#cde8df,stroke:#009E73,color:#222
  style TCS fill:#f6d2c4,stroke:#D55E00,color:#222
  style CAR fill:#f3d5e6,stroke:#CC79A7,color:#222
```

## 图 1 时序

```mermaid
sequenceDiagram
  participant SAP as SAP ERP
  participant WMS as WMS
  participant TCS as openTCS
  participant CAR as 车辆
  WMS->>SAP: 查询未过账入库交货
  SAP-->>WMS: 交货 1800000001，工厂 1000，库存地点 FG00
  Note over WMS: 在收货位登记托盘 4711，创建上架运输单
  WMS->>TCS: 下订单 INB-1800000001
  TCS->>CAR: 派车，收货位装货，存储位卸货
  CAR-->>TCS: 回报位置，直到完成
  TCS-->>WMS: FINISHED
  WMS->>SAP: PostGoodsReceipt，带令牌和 If-Match
  SAP-->>WMS: 状态改为 C，物料凭证 5000000001
```

## 图 2 收货过账

WMS 调用 `PostGoodsReceipt` 时，SAP 按这个顺序检查。有一项不通过就不改交货单。

```mermaid
flowchart TB
  IN["WMS 调用 PostGoodsReceipt<br/>交货 1800000001"]
  C1{"CSRF 令牌正确？"}
  C2{"带了 If-Match？"}
  C3{"ETag 是最新的？"}
  C4{"交货单存在？"}
  C5{"状态还是 A？"}
  OK["200，状态改为 C"]
  E1["403 拒绝"]
  E2["428 缺少前提"]
  E3["412 ETag 过期"]
  E4["404 没有这张交货"]
  E5["400 已经过过账"]
  IN --> C1
  C1 -->|否| E1
  C1 -->|是| C2
  C2 -->|否| E2
  C2 -->|是| C3
  C3 -->|否| E3
  C3 -->|是| C4
  C4 -->|否| E4
  C4 -->|是| C5
  C5 -->|否| E5
  C5 -->|是| OK
  style IN fill:#cde8df,stroke:#009E73,color:#222
  style OK fill:#cde8df,stroke:#009E73,color:#222
  style E1 fill:#f6d0d4,stroke:#B2182B,color:#222
  style E2 fill:#f6d0d4,stroke:#B2182B,color:#222
  style E3 fill:#f6d0d4,stroke:#B2182B,color:#222
  style E4 fill:#f6d0d4,stroke:#B2182B,color:#222
  style E5 fill:#f6d0d4,stroke:#B2182B,color:#222
```

货在车上的时候，SAP 交货仍是 `A`。SAP 看不到仓位。

## 图 3 分层

```mermaid
flowchart TB
  L4["L4 SAP<br/>入库交货 1800000001<br/>MAT-100 × 1 EA · 1000 / FG00"]
  L3["L3 WMS<br/>直接调 SAP<br/>托盘 4711：收货位到存储位"]
  L2["L2 openTCS<br/>INB-1800000001<br/>收货位装货，存储位卸货"]
  L1["L1 车辆<br/>Point-Park 到 Point-GR 再到 Point-Store"]
  L3 -->|"读取交货、回写收货"| L4
  L3 -->|"派上架"| L2 --> L1
  style L4 fill:#d6e6f5,stroke:#0072B2,color:#222
  style L3 fill:#cde8df,stroke:#009E73,color:#222
  style L2 fill:#f6d2c4,stroke:#D55E00,color:#222
  style L1 fill:#f3d5e6,stroke:#CC79A7,color:#222
```

| 阶段 | SAP | WMS | openTCS | 车辆 |
| --- | --- | --- | --- | --- |
| 等待 | A | 还没有单 | 还没有单 | IDLE |
| 上架 | A | STARTED | BEING_PROCESSED | 执行中 |
| 过账 | C | FINISHED | FINISHED | IDLE |
| 停手 | A | ONFAILURE | FAILED | 停 |

<details>
<summary>高清原图</summary>

[接口](docs/sap-erp-wms/fig1_interfaces_zh.png) · [时序](docs/sap-erp-wms/fig2_sequence_zh.png) · [收货过账](docs/sap-erp-wms/fig3_confirm_zh.png) · [架构](docs/sap-erp-wms/fig4_architecture_zh.png)

同目录还有英文版、PDF 和 SVG。

</details>
