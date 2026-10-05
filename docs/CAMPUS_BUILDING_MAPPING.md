# 西南大学教学楼位置映射初版

> 后续已接入现场记录的地图选楼，并补充本地道路/建筑背景，见 [自动处理与地图改进](SITE_CONTEXT_REFINEMENT.md)。编号覆盖仍为14栋；下文是初版独立映射的历史记录。

2026-10-02 完成位置到附近楼栋的独立映射模块与本机核对页面。未接入现有现场拍照页面，未修改数据库、当前课表配置或 youziauth 项目。

## 来源与当前覆盖

- 坐标、轮廓、楼栋数字编号来自 [OpenStreetMap 原始 API 响应](https://api.openstreetmap.org/api/0.6/map?bbox=106.411,29.813,106.436,29.841)，原始响应保存在 `output/2026-10-02-campus-building-mapping/osm-campus-raw.xml`。
- 导入14栋：7、8、10、14、22、25、26、27、28、29、32、33、37、38。37教的编号来自同一 OSM 建筑的明确 `alt_name`。没有按学院名称猜楼号。
- 每栋保存 OSM URL、版本、更新时间、轮廓、编号别名；统一标记 `public_map_unverified`。这是可核对的社区地图资料，未经过校内现场测量，不能说已经核实地理准确率。
- [8教原始对象](https://www.openstreetmap.org/way/843421763)、[25教原始对象](https://www.openstreetmap.org/way/843465238)。数据许可为 ODbL-1.0，展示保留 © OpenStreetMap contributors 署名及[许可链接](https://www.openstreetmap.org/copyright)。
- 对照工作区 `2025-2026-2全校课表260413.xlsx` 的非荣昌场地数字前缀：34个前缀中11个已有地图对应。该工作簿只用于编号覆盖审计，不代表本次授权切换当前学期。其余23个为：1、2、3、4、5、6、9、11、15、16、17、19、23、24、30、31、35、39、40、46、48、86、99。86/99等是否实际教学楼还需核实，不能把前缀数直接当作真实建筑数。
- 荣昌校区、无楼号场地、线上课程和未编号学院建筑不套用北碚的楼号坐标。覆盖明细见输出目录的 `coverage.json`。

学校公开资料列出了 `map.swu.edu.cn`，但本次 HTTPS 请求发生 SSL EOF，HTTP 返回502；未能取得校方 GIS 兴趣点，因此目前没有全校完整覆盖。

## 与 youziauth 的结合方式

参考 [youziauth 地图选点文档](https://github.com/yoouzic/youziauth/blob/main/docs/simulation-map-picker.md) 的 WGS84 选点、GCJ02 底图分离及模拟点工作方式。本次使用同类 `{latitude, longitude, accuracy}` 输入，可接入其 WGS84 保存选点的数据；不会把宿舍默认点当作教学楼点。没有导入它的登录、打卡提交、随机漂移或 Windows 定位依赖。

所有建筑与位置输入使用 WGS84。浏览器位置或 youziauth 保存的 WGS84 选点可直接输入；高德 GCJ02 选点须在适配边界明确转换，本模块拒绝标为 GCJ02 的输入，避免静默混用。模拟点只用于试验，不表示手机采样或校方认可的位置。

## 映射规则

`app/services/campus_buildings.py` 不依赖 Flask、数据库或在线地图服务。

1. 有效输入须包含有限数值的经纬度和正的定位精度，拒绝布尔值、NaN、越界及缺字段。未取得定位时返回空建议，保留手动选楼。
2. 使用以观察点为原点的局部米制投影，计算到建筑外轮廓的最短距离；建筑内部/边界距离为0。适用于本校园尺度，不作为全球地理算法。
3. 候选范围为 `max(250, accuracy + 50)` 米。250米是待现场校准的邻近缓冲，不能解释为模型置信区间或召回率保证。误差大于1000米时直接提示手动选楼。
4. 返回范围内的全部已映射建筑，按距离排序，不截成前三栋，不自动确认。调用者可先展示少数选项，但必须让人看到“更多教学楼”。
5. `allowed_buildings` 可传当前权威课表的规范楼号，限制推荐来源，并返回尚无坐标的 `unmapped_buildings`。位置不能用于删除这些尚未映射的课表楼栋。
6. 楼号前导零规范为数字编号，例如 `08 → 8`。接入课表时可组合 `8` 与门牌 `0609`，再交给现有 `normalize_room` 形成 `8-609`；不能从门牌推断楼号。

调用示例：

```python
from app.services.campus_buildings import nearby_buildings
result = nearby_buildings(
    {'latitude': 29.825104006666667, 'longitude': 106.42000534666666, 'accuracy': 35},
    campus='beibei', allowed_buildings=['7', '8', '14', '25', '27', '28', '46'],
)
# result['buildings'] 为楼栋建议；46列入 unmapped_buildings，仍允许人工选46。
```

## 回放与验证

`tests/test_campus_buildings.py` 17项测试通过，包括建筑内部、轮廓距离、8教偏到25教、低精度扩大范围、非法/失败定位、校外位置、跨校区隔离、未映射课表楼栋及坐标系不一致。

在25教轮廓内模拟35米精度，搜索范围250米，返回25、29、28、27、26、8、7教；8教距离约191.4米，排名第6。这支持保留附近备选楼栋，但不足以证明“前三项必有正确楼”。门牌和当天课表仍需后续排序。

在8教内模拟35米精度，返回8、7、14、25教；精度800米时范围扩大到850米，25教附近返回10栋。校外/失败/极粗定位均不强行推荐最近楼。完整结果在 `replay-results.json`。

真实浏览器验证了8教、偏到25教、800米精度和失败场景的可见结果。已保存桌面与手机宽度截图；390像素视口检查未发现水平溢出。仅验证本机模拟交互，尚未完成手机现场定位验证。

启动与重放：

```powershell
.venv-audit/Scripts/python.exe tools/campus_mapping_preview.py --port 5098
# http://127.0.0.1:5098，仅绑定本机
.venv-audit/Scripts/python.exe tools/campus_mapping_preview.py --replay-output output/2026-10-02-campus-building-mapping/replay-results.json
.venv-audit/Scripts/python.exe -m pytest tests/test_campus_buildings.py -q
```

下一步是核对这14栋的名称与轮廓，再以校方GIS资料或明确标注、人工核对的地图选点补齐剩余楼栋。之后再把建议接到现有现场记录的教学楼选项，不改变人工确认与定位失败仍可保存的规则。
