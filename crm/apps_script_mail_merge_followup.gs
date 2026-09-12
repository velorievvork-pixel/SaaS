// Опциональный скрипт: рассылка первого письма + follow-up через 3-4 дня без ответа.
// Вставьте в тот же проект Apps Script (отдельным файлом), что и
// apps_script_followup_reminder.gs. Требует те же колонки в листе "Лиды" плюс
// Contact_Channel, Contact_Value, Personalized_Line, Last_Message_Date, Notes.
//
// После вставки запустите setupMailTriggers один раз.

const MAIL_SHEET_NAME = 'Лиды';
const FOLLOWUP_AFTER_DAYS = 3;
const SENDER_NAME = 'Ваше Имя'; // <- замените

const MCOL = {
  Company: 'Company',
  Domain: 'Domain',
  ContactName: 'Contact_Name',
  ContactChannel: 'Contact_Channel',
  ContactValue: 'Contact_Value',
  PersonalizedLine: 'Personalized_Line',
  Status: 'Status',
  StatusDate: 'Status_Date',
  LastMessageDate: 'Last_Message_Date',
  Notes: 'Notes',
};

function setupMailTriggers() {
  ScriptApp.newTrigger('sendFirstMessages')
    .timeBased()
    .everyDays(1)
    .atHour(10)
    .create();

  ScriptApp.newTrigger('sendFollowups')
    .timeBased()
    .everyDays(1)
    .atHour(11)
    .create();

  Logger.log('Триггеры рассылки настроены.');
}

function mailColIndexes_(sheet) {
  const headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  const idx = {};
  headers.forEach((h, i) => { idx[h] = i + 1; });
  return idx;
}

function buildFirstMessage_(contactName, personalizedLine) {
  return `${personalizedLine}\n\n` +
    `Меня зовут ${SENDER_NAME}, помогаю B2B SaaS-компаниям выстраивать поток первых ` +
    `клиентов через таргетированные холодные продажи — как отдельная функция на аутсорсе, ` +
    `без найма своего SDR.\n\n` +
    `Было бы полезно обсудить 15 минут на этой неделе?`;
}

function buildFollowupMessage_(contactName) {
  return `Хотел на всякий случай поднять предыдущее письмо — вдруг затерялось.\n\n` +
    `Если сейчас не в приоритете, дайте знать, и я не буду больше писать.`;
}

// Отправляет первое сообщение всем со статусом "Новый" и каналом email.
function sendFirstMessages() {
  const sheet = SpreadsheetApp.getActive().getSheetByName(MAIL_SHEET_NAME);
  const idx = mailColIndexes_(sheet);
  const data = sheet.getDataRange().getValues();
  const now = new Date();

  for (let row = 1; row < data.length; row++) {
    const status = data[row][idx[MCOL.Status] - 1];
    const channel = data[row][idx[MCOL.ContactChannel] - 1];
    const contactValue = data[row][idx[MCOL.ContactValue] - 1];

    if (status !== 'Новый' || channel !== 'email' || !contactValue) continue;

    const company = data[row][idx[MCOL.Company] - 1];
    const contactName = data[row][idx[MCOL.ContactName] - 1];
    const line = data[row][idx[MCOL.PersonalizedLine] - 1];

    GmailApp.sendEmail(
      contactValue,
      `${company} + помощь с первыми клиентами`,
      buildFirstMessage_(contactName, line)
    );

    const sheetRow = row + 1;
    sheet.getRange(sheetRow, idx[MCOL.Status]).setValue('Написано');
    sheet.getRange(sheetRow, idx[MCOL.StatusDate]).setValue(now);
    sheet.getRange(sheetRow, idx[MCOL.LastMessageDate]).setValue(now);
  }
}

// Отправляет follow-up тем, кто "Написано" более FOLLOWUP_AFTER_DAYS дней назад
// и ещё не получал follow-up (проверяем через Notes, чтобы не отправить дважды).
function sendFollowups() {
  const sheet = SpreadsheetApp.getActive().getSheetByName(MAIL_SHEET_NAME);
  const idx = mailColIndexes_(sheet);
  const data = sheet.getDataRange().getValues();
  const now = new Date();

  for (let row = 1; row < data.length; row++) {
    const status = data[row][idx[MCOL.Status] - 1];
    const channel = data[row][idx[MCOL.ContactChannel] - 1];
    const contactValue = data[row][idx[MCOL.ContactValue] - 1];
    const lastMessageDate = data[row][idx[MCOL.LastMessageDate] - 1];
    const notes = data[row][idx[MCOL.Notes] - 1] || '';

    if (status !== 'Написано' || channel !== 'email' || !contactValue) continue;
    if (!lastMessageDate || notes.indexOf('[followup_sent]') !== -1) continue;

    const daysSince = (now - new Date(lastMessageDate)) / (1000 * 60 * 60 * 24);
    if (daysSince < FOLLOWUP_AFTER_DAYS) continue;

    const company = data[row][idx[MCOL.Company] - 1];
    const contactName = data[row][idx[MCOL.ContactName] - 1];

    GmailApp.sendEmail(
      contactValue,
      `Re: ${company} + помощь с первыми клиентами`,
      buildFollowupMessage_(contactName)
    );

    const sheetRow = row + 1;
    sheet.getRange(sheetRow, idx[MCOL.LastMessageDate]).setValue(now);
    sheet.getRange(sheetRow, idx[MCOL.Notes]).setValue(notes + ' [followup_sent]');
  }
}
