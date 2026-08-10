import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = path.resolve(process.argv[2] ?? path.dirname(new URL(import.meta.url).pathname));
const previewDir = path.join(outputDir, ".previews");

function row(width, values) {
  const result = Array(width).fill(null);
  for (const [index, value] of Object.entries(values)) result[Number(index)] = value;
  return result;
}

function writeRows(sheet, startRow, rows) {
  const width = Math.max(...rows.map((items) => items.length));
  sheet.getRangeByIndexes(startRow - 1, 0, rows.length, width).values = rows;
}

function styleSheet(sheet, headerRow, width) {
  sheet.showGridLines = false;
  sheet.getRangeByIndexes(headerRow - 1, 0, 1, width).format = {
    fill: "#334155",
    font: { bold: true, color: "#FFFFFF" },
  };
  sheet.getRangeByIndexes(headerRow, 0, 4, width).format = {
    borders: { preset: "insideHorizontal", style: "thin", color: "#CBD5E1" },
  };
  sheet.freezePanes.freezeRows(headerRow);
}

async function saveWorkbook(workbook, filename, sheetNames) {
  await fs.mkdir(outputDir, { recursive: true });
  await fs.mkdir(previewDir, { recursive: true });
  for (const sheetName of sheetNames) {
    const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
    await fs.writeFile(
      path.join(previewDir, `${filename.replace(/\.xlsx$/, "")}-${sheetName}.png`),
      new Uint8Array(await preview.arrayBuffer()),
    );
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path.join(outputDir, filename));
}

function buildHistoricalSchool() {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("合成历史课表");
  const width = 35;
  const headers = row(width, {
    1: "课程名称",
    4: "任课教师",
    7: "教师所在学院",
    13: "教学班组成",
    15: "专业组成",
    19: "周次",
    21: "星期几",
    23: "上课节次",
    26: "上课地点",
  });
  const validA = row(width, {
    1: "合成课程甲", 4: "合成教师甲", 7: "合成信息学院", 13: "2024级合成一班",
    15: "合成专业", 19: "1-16周(双)", 21: "星期一", 23: "1-2节", 26: "合成教室A",
  });
  const invalid = row(width, {
    1: "合成课程坏行", 4: "合成教师乙", 7: "合成信息学院", 13: "2024级合成一班",
    15: "合成专业", 19: "1-16周", 21: "星期八", 23: "3-4节", 26: "合成教室B",
  });
  const validB = row(width, {
    1: "合成课程乙", 4: "合成教师丙", 7: "合成理学院", 13: "2024级合成二班",
    15: "合成数学专业", 19: "2-10周", 21: "星期三", 23: "5-6节", 26: "合成教室C",
  });
  writeRows(sheet, 1, [
    row(width, { 0: "合成历史课表夹具" }),
    headers,
    validA,
    invalid,
    validA,
    validB,
  ]);
  sheet.getRangeByIndexes(0, 197, 1, 1).values = [["格式残留不属于业务列"]];
  const notes = workbook.worksheets.add("无关说明");
  writeRows(notes, 1, [["本工作表仅用于验证导入器选择业务表"]]);
  styleSheet(sheet, 2, width);
  styleSheet(notes, 1, 1);
  return { workbook, sheetNames: ["合成历史课表", "无关说明"] };
}

function buildCurrentSchool() {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("合成当前课表");
  const width = 23;
  const headers = row(width, {
    0: "课程", 2: "教师姓名", 5: "开课学院", 8: "教学班", 10: "专业",
    14: "上课周", 16: "星期", 18: "节次", 21: "教室",
  });
  const valid = row(width, {
    0: "合成当前课程", 2: "合成当前教师", 5: "合成信息学院", 8: "2024级合成一班",
    10: "合成专业", 14: "3-12周", 16: "周二", 18: "第3、4节", 21: "合成教室D",
  });
  writeRows(sheet, 1, [
    row(width, { 0: "合成当前课表夹具" }),
    headers,
    valid,
  ]);
  sheet.getRangeByIndexes(0, 197, 1, 1).values = [["格式残留不属于业务列"]];
  const notes = workbook.worksheets.add("说明页");
  writeRows(notes, 1, [["与业务数据无关的合成说明"]]);
  styleSheet(sheet, 2, width);
  styleSheet(notes, 1, 1);
  return { workbook, sheetNames: ["合成当前课表", "说明页"] };
}

function buildClassMapping() {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("合成班级映射");
  const headers = ["信息员编号", "学号", "行政班", "备注"];
  writeRows(sheet, 1, [
    headers,
    ["SYN-L-001", null, "2024级合成一班", "按编号解析"],
    [null, "SYN-STU-0002", "2024级合成二班", "按学号解析"],
    [null, null, "2024级合成坏班", "缺少身份，应该隔离"],
  ]);
  const notes = workbook.worksheets.add("其他页");
  writeRows(notes, 1, [["合成映射说明"]]);
  styleSheet(sheet, 1, headers.length);
  styleSheet(notes, 1, 1);
  return { workbook, sheetNames: ["合成班级映射", "其他页"] };
}

function buildPersonalSchedule() {
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("合成个人课表");
  const headers = ["信息员编号", "学号", "课程名称", "教学周", "星期几", "第几节"];
  const exact = ["SYN-L-001", null, "合成课程甲", "1-16周(双)", "星期一", "1-2节"];
  writeRows(sheet, 1, [
    ["合成个人课表夹具"],
    headers,
    exact,
    exact,
    [null, "SYN-STU-0002", "合成课程乙", "3-12周", "周三", "5-6节"],
  ]);
  const notes = workbook.worksheets.add("无关页");
  writeRows(notes, 1, [["个人课表合成说明"]]);
  styleSheet(sheet, 2, headers.length);
  styleSheet(notes, 1, 1);
  return { workbook, sheetNames: ["合成个人课表", "无关页"] };
}

const fixtures = [
  ["school_schedule_historical.xlsx", buildHistoricalSchool],
  ["school_schedule_current.xlsx", buildCurrentSchool],
  ["listener_class_mapping.xlsx", buildClassMapping],
  ["personal_schedule.xlsx", buildPersonalSchedule],
];

for (const [filename, builder] of fixtures) {
  const { workbook, sheetNames } = builder();
  await saveWorkbook(workbook, filename, sheetNames);
  console.log(`generated ${filename} with ${sheetNames.length} synthetic sheets`);
}
