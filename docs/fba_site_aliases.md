# FBA 地址与新旧代码共用规则

2026-09-16 更新。用户提供的新旧代码对应表是别名关系来源；PPO4 的新代码为 tbd，未创建无效别名。IUTJ 地址由用户直接提供。

`delivery_fba_aliases.csv` 的新代码从原代码继承地址、州、邮编，不删除旧代码，也不改变数据中的仓点名称或合并统计。未来若单独维护新代码地址，则以新代码独立记录为准。旧代码缺失时不猜测地址。

| 旧代码 | 新代码 | 州 | ZIP | 地址来源与核对说明 |
| --- | --- | --- | --- | --- |
| MEM6 | IMS1 | MS | 38654 | [OSHA 现场检查](https://www.osha.gov/ords/imis/establishment.inspection_detail?id=1798726.015)：11505 Progress Way, Olive Branch |
| MCI3 | IMO1 | MO | 64068 | [FBA Finder](https://fba-finder.com/usa/missouri/mci3/) 与 [Waze](https://www.waze.com/live-map/directions/amazon-mci3-s-withers-rd-2361-liberty?to=place.w.174064008.1740312400.8944661) 均为 2361 S Withers Rd, Liberty；[OSHA Amazon 场地清单](https://www.osha.gov/sites/default/files/2024-12/12202024-OSHA-Amazon-Ergo-Agreement-Fully-Executed-Public-Facing-Addresses-Redacted.pdf) 核对城市及 ZIP |
| MWH1 | IWA6 | WA | 99301 | [华盛顿州场地档案](https://apps.ecology.wa.gov/facilitysite/FacilitySite/FacilitySiteReport/99997668)：1202 S Road 40 East, Pasco，明确同时列出 MWH1 / IWA6 |
| SAN6 | ICA1 | CA | 92154 | [加州水资源监管场地档案](https://ciwqs.waterboards.ca.gov/ciwqs/readOnly/CiwqsReportServlet?placeID=S907803&reportName=facilityAtAGlance)：6940 Otay Mesa Road, San Diego；采用政府场地记录，不采用论坛中的 6980 门牌 |
| GEU5 | IAZ1 | AZ | 85395 | [Amazon 员工通勤地址表](https://m.media-amazon.com/images/G/01/wfs/CommuterServices_Shuttle_Bus_List_1-1.pdf)：4660 North Cotton Lane, Goodyear；[货代仓库清单](https://www.autochina-logistics.com/fbacangku/meiguo/25819.html) 及 [海关运输记录](https://www.importgenius.com/importers/amazon-warehouse-geu5) 核对 ZIP |
| SAV3 | IGA3 | GA | 31216 | [卖家提供的 Amazon 发货地址](https://sellercentral.amazon.co.uk/seller-forums/discussions/t/66a296b4-e892-4be0-85ff-e58054b2be83?postId=50d1f38d-9869-4154-b29b-51756d7588be) 与 [公开法院附件](https://cases.stretto.com/public/x268/12466/PLEADINGS/1246608222380000000027.pdf)：7001 Skipper Rd, Macon；不采用仓库百科旧的街道范围 |
| AMA1 | ITX3 | TX | 79108 | [OSHA 现场检查](https://www.osha.gov/ords/imis/establishment.inspection_detail?id=1668102.015)：8590 NE 24th Ave, Amarillo；不采用部分旧商业清单的 Lakeside 地址 |
| SJC7 | ICA3 | CA | 95377 | [Amazon 员工通勤地址表](https://m.media-amazon.com/images/G/01/wfs/CommuterServices_Shuttle_Bus_List_1-1.pdf)：188 S Mountain House Parkway, Tracy；[货代清单](https://www.torbon.com/amz-whaddr/) 与 [仓库清单](https://www.youramazonguy.com/amazon-address-list/) 核对 ZIP |
| WBW2 | IPA1 | PA | 18447 | [卖家提供的 Amazon 发货地址](https://sellercentral.amazon.com/seller-forums/discussions/t/1c86b49a-ce71-4f35-afbd-d06bd1eca2da?postId=1c86b49a-ce71-4f35-afbd-d06bd1eca2da) 与 [仓库清单](https://abfba.com/amazon-warehouse-master-list)：1300 Corporate Way, Olyphant；OSHA 场地清单核对城市及 ZIP |
| LAN2 | IMI1 | MI | 48917 | 沿用本项目原记录：6500 W Mt Hope Hwy, Lansing, MI |
| RDU4 | INC1 | NC | 28303 | 沿用本项目原记录：6309 Bragg Blvd, Fayetteville, NC |
| — | IUTJ | CA | 92335 | 用户提供：9253 Dreamland Drive, Fontana, CA 92335, USA |

本表记录物理仓点地址；公开信息中的临时包裹转送地点不替换仓点地址。本次未更新 FBX 地址库。
