// Вставьте этот код целиком в Extensions -> Apps Script вашей Google-таблицы.
// После вставки один раз запустите функцию setupTriggers (выберите её в выпадающем
// списке наверху редактора и нажмите Run) — Google попросит разрешения, это нормально.

const SHEET_NAME = 'Лиды';
const NOTIFY_EMAIL = 'you@example.com'; // <- замените на свою почту
const STALE_DAYS = 5;
const DONE_STATUSES = ['Клиент', 'Отказ'];

const COL = {
  Company: 'Company',
  Status: 'Status',
  StatusDate: 'Status_Date',
};

function setupTriggers() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('onEditStatusColumn')
    .forSpreadsheet(SpreadsheetApp.getActive())
    .onEdit()
    .create();

  ScriptApp.newTrigger('checkStaleLeads')
    .timeBased()
    .everyDays(1)
    .atHour(9)
    .create();

  Logger.log('Триггеры настроены.');
}

function getColumnIndexes_(sheet) {
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const idx = {};
  headers.forEach((h, i) => { idx[h] = i + 1; }); // 1-indexed для getRange
  return idx;
}

function onEditStatusColumn(e) {
  const sheet = e.range.getSheet();
  if (sheet.getName() !== SHEET_NAME) return;

  const idx = getColumnIndexes_(sheet);
  if (e.range.getColumn() !== idx[COL.Status]) return;
  if (e.range.getRow() === 1) return;

  sheet.getRange(e.range.getRow(), idx[COL.StatusDate])
    .setValue(new Date());
}

function checkStaleLeads() {
  const sheet = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
  const idx = getColumnIndexes_(sheet);
  const data = sheet.getDataRange().getValues();

  const now = new Date();
  const stale = [];

  for (let row = 1; row < data.length; row++) {
    const status = data[row][idx[COL.Status] - 1];
    const statusDate = data[row][idx[COL.StatusDate] - 1];
    const company = data[row][idx[COL.Company] - 1];

    if (!status || DONE_STATUSES.indexOf(status) !== -1) continue;
    if (!statusDate) continue;

    const daysSince = (now - new Date(statusDate)) / (1000 * 60 * 60 * 24);
    if (daysSince > STALE_DAYS) {
      stale.push(`${company} — статус "${status}" уже ${Math.floor(daysSince)} дней`);
    }
  }

  if (stale.length === 0) return;

  MailApp.sendEmail(
    NOTIFY_EMAIL,
    `SDR CRM: ${stale.length} лид(ов) зависли больше ${STALE_DAYS} дней`,
    stale.join('\n')
  );
}
