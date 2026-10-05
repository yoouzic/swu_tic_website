# 全课表教学楼外观模型 — 2026-10-02

## 范围与结果

本轮以隔离预览所用的当前权威课表快照为依据：2025-2026-2、batch_id=1、来源文件《2025-2026-2全校课表260413.xlsx》，共16013行。按现有楼号解析规则，从location_normalized提取楼号。用户明确要求忽略F、G、MC1、MC2、99。

排除后30个楼号均有非空、独立的照片参考外观网格。4和19分别提供A/B分部切换。既有地图数据只覆盖其中11个；另外19个在选中时显示独立的模型预览，不添加猜测坐标。未修改地图数据文件、原数据库、学校课表或正式表单。

新增模型为轻量三维点面定义，经SVG等轴投影显示。楼高、比例、窗格数量及照片遮挡部分均为简化表达。10教主要依据内庭照片，24教仅有树木遮挡的远景；4/19分部与课表教室尚无自动对应关系，只切换外观展示。

## 清单

|楼号|名称|展示方式|官网来源|
|---|---|---|---|
|1|雨僧楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2395.htm)|
|3|绩镛楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2393.htm)|
|4|咏修楼 A栋|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2392.htm)|
|5|弘礼楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2376.htm)|
|6|师元楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2333.htm)|
|7|修业楼|地图模型|[官网](https://www.swu.edu.cn/info/1153/2345.htm)|
|8|敬德楼|地图模型|[官网](https://www.swu.edu.cn/info/1153/2368.htm)|
|9|荟文楼 A栋|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2349.htm)|
|10|荟文楼 B栋|地图模型|[官网](https://www.swu.edu.cn/info/1153/16887.htm)|
|11|田家炳教育书院|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2334.htm)|
|14|白南楼|地图模型|[官网](https://www.swu.edu.cn/info/1153/2390.htm)|
|15|至美楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/16901.htm)|
|16|求实楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2354.htm)|
|17|漱溟楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2389.htm)|
|19|厚乐楼 A栋|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/16902.htm)|
|23|润心楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2360.htm)|
|24|启智楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2361.htm)|
|25|明德楼|地图模型|[官网](https://www.swu.edu.cn/info/1153/2366.htm)|
|27|弘信楼|地图模型|[官网](https://www.swu.edu.cn/info/1153/2373.htm)|
|28|弘义楼|地图模型|[官网](https://www.swu.edu.cn/info/1153/2375.htm)|
|30|阳初楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2388.htm)|
|31|兆畦楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2387.htm)|
|32|弘勤楼|地图模型|[官网](https://www.swu.edu.cn/info/1153/2372.htm)|
|33|同庆楼|地图模型|[官网](https://www.swu.edu.cn/info/1153/2386.htm)|
|35|逸夫楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2331.htm)|
|37|弘朴楼|地图模型|[官网](https://www.swu.edu.cn/info/1153/2371.htm)|
|38|明辨楼|地图模型|[官网](https://www.swu.edu.cn/info/1153/2341.htm)|
|39|弘忠楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2370.htm)|
|40|弘实楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2369.htm)|
|48|弘新楼|独立外观预览，位置待核对|[官网](https://www.swu.edu.cn/info/1153/2357.htm)|

## 验证与产物

- 37项JavaScript回归 + 14项Chromium主题回归全部通过。
- 实际Flask隔离预览逐栋选择30个楼号，全部存在模型面；390px再次逐栋选择30个，无水平溢出。
- 已检查4/19的A/B切换、源链接和位置缺失时独立预览；未点击提交或确认到原数据。
- 全量模型总览all-models.html为独立文件，不需要原账户或本地接口；包含30张模型卡和分部切换。
- output/2026-10-02-all-schedule-buildings：scope.json、official-sources.json、coverage.json、models/*.json、browser-checks.json、mobile-checks.json和截图。
- 本地验收服务为127.0.0.1:5107（复制数据库）；模型总览为127.0.0.1:5106/all-models.html，关闭服务后仍可打开本地HTML文件。

地图轮廓补齐尚未完成，不能把30/30外观模型称为30/30地图定位。原始OSM还含30/31教relation与田家炳教育书院等潜在线索，需另行处理多环轮廓与来源核对，不在本轮擅自拼接。
