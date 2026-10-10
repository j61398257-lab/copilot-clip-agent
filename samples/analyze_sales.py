import csv
from collections import defaultdict
from pathlib import Path


def main() -> None:
    path = Path(__file__).with_name("sales.csv")
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for record in csv.DictReader(handle):
            quantity = int(record["销量"])
            price = float(record["单价"])
            rows.append({
                "品类": record["品类"],
                "商品": record["商品"],
                "销量": quantity,
                "金额": quantity * price,
            })

    by_category = defaultdict(lambda: {"销量": 0, "金额": 0.0})
    for record in rows:
        bucket = by_category[record["品类"]]
        bucket["销量"] += record["销量"]
        bucket["金额"] += record["金额"]

    total_quantity = sum(record["销量"] for record in rows)
    total_amount = sum(record["金额"] for record in rows)
    top = max(rows, key=lambda record: record["金额"])

    print("销售汇总")
    print("记录数：%d" % len(rows))
    print("总销量：%d" % total_quantity)
    print("总金额：%.2f" % total_amount)
    print("")
    print("按品类")
    for name in sorted(by_category):
        bucket = by_category[name]
        print("%s\t销量 %d\t金额 %.2f" % (name, bucket["销量"], bucket["金额"]))
    print("")
    print(
        "单行金额最高：%s %s，销量 %d，金额 %.2f"
        % (top["品类"], top["商品"], top["销量"], top["金额"])
    )


if __name__ == "__main__":
    main()
