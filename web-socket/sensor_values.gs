function forceAuth() {
  // A completely unprotected call to force Google's security to step in
  UrlFetchApp.fetch("https://google.com");
}

function doGet(e) {
  var sheetName = "Sheet1";
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(sheetName);

  if (!sheet) return ContentService.createTextOutput("Error: Sheet not found").setMimeType(ContentService.TEXT);

  // 1. DATA READ MODE (If no parameters or read parameter exists)
  if (!e.parameter || Object.keys(e.parameter).length === 0 || e.parameter.read) {
    var lastRow = sheet.getLastRow();
    if (lastRow < 2) return ContentService.createTextOutput("No data").setMimeType(ContentService.MimeType.TEXT);
    
    if (e.parameter.read === "history") {
      var numRows = Math.min(20, lastRow - 1);
      var startRow = lastRow - numRows + 1;
      var values = sheet.getRange(startRow, 1, numRows, 20).getValues();
      var history = values.map(function(data) {
        return {
          date: data[0], 
          time: data[1],
          time_in_seconds: data[2],
          time_diff: data[3],
          sin_time: data[4],
          cos_time: data[5],
          occupancy: data[6], 
          humidity: data[7], 
          temperature: data[8], 
          tout: data[9],
          deltaT: data[10],
          deltaH: data[11],
          energy_wh: data[12],
          energy_kwh: data[13],
          energy_ws: data[14],
          power: data[15],   
          pf: data[16],      
          energy_pzem: data[17],
          voltage: data[18], 
          current: data[19]
        };
      });
      return ContentService.createTextOutput(JSON.stringify(history))
        .setMimeType(ContentService.MimeType.JSON);
    }
    
    // Default: read single latest row
    var data = sheet.getRange(lastRow, 1, 1, 20).getValues()[0];
    return ContentService.createTextOutput(JSON.stringify({
      date: data[0], 
      time: data[1],
      time_in_seconds: data[2],
      time_diff: data[3],
      sin_time: data[4],
      cos_time: data[5],
      occupancy: data[6], 
      humidity: data[7], 
      temperature: data[8], 
      tout: data[9],
      deltaT: data[10],
      deltaH: data[11],
      energy_wh: data[12],
      energy_kwh: data[13],
      energy_ws: data[14],
      power: data[15],   
      pf: data[16],      
      energy_pzem: data[17],
      voltage: data[18], 
      current: data[19]  
    })).setMimeType(ContentService.MimeType.JSON);
  }

  // 2. DATA WRITE MODE
  var rowData = Array(20).fill("");
  
  // Directly parsed values mapped into their requested order
  rowData[0] = "'" + (e.parameter.date || "");
  rowData[1] = "'" + (e.parameter.time || "");
  rowData[6] = parseInt(e.parameter.occupancy) === 1 ? 1 : 0;
  rowData[7] = parseFloat(e.parameter.humidity) || 0;
  rowData[8] = parseFloat(e.parameter.temperature) || 0;
  
  // Fetch Outside Temperature using the 15-minute optimization cache
  var Tout = getCachedTout();
  rowData[9] = Tout; // Column J
  
  // Hardware electrical parameters mapped to trailing columns
  rowData[15] = parseFloat(e.parameter.power) || 0;
  rowData[16] = parseFloat(e.parameter.pf) || 0;
  rowData[17] = parseFloat(e.parameter.energy) || 0;
  rowData[18] = parseFloat(e.parameter.voltage) || 0;
  rowData[19] = parseFloat(e.parameter.current) || 0;

  // Time calculations
  var timeInSeconds = convertTimeToSeconds(e.parameter.time);
  rowData[2] = timeInSeconds; // Column C

  // 24-HOUR CYCLICAL TIME ENCODING
  var dailySeconds = timeInSeconds % 86400; 
  var angle = (2 * Math.PI * dailySeconds) / 86400;
  
  rowData[4] = Math.sin(angle).toFixed(6); // Column E
  rowData[5] = Math.cos(angle).toFixed(6); // Column F

  var lastRow = sheet.getLastRow();
  if (lastRow >= 2) {
    // Collect previous raw sequential time from Column C (Sheet Column 3)
    var prevTime = sheet.getRange(lastRow, 3).getValue() || timeInSeconds;
    var timeDiff = timeInSeconds - prevTime;
    if (timeDiff < 0) timeDiff = 0; // Midnight handling
    
    rowData[3] = timeDiff; // Column D
    
    // Energy Step Integration Formula calculations
    var pcalc = rowData[15]; 
    var energyWs = pcalc * timeDiff;
    var energyWh = energyWs / 3600;
    var energyKwh = energyWh / 1000;
    
    rowData[12] = energyWh.toFixed(4);  // Column M
    rowData[13] = energyKwh.toFixed(6); // Column N
    rowData[14] = energyWs.toFixed(2);  // Column O

    // ==================== FIXED: TRUE 1-MINUTE DELTA CALCULATIONS ====================
    var lookbackRow = Math.max(2, lastRow - 5);
if (lookbackRow <= lastRow) {
  var oldTemp = parseFloat(sheet.getRange(lookbackRow, 9).getValue());
  var oldHumid = parseFloat(sheet.getRange(lookbackRow, 8).getValue());

  if (isNaN(oldTemp)) oldTemp = rowData[8];
  if (isNaN(oldHumid)) oldHumid = rowData[7];

  rowData[10] = (rowData[8] - oldTemp).toFixed(2);
  rowData[11] = (rowData[7] - oldHumid).toFixed(2);
} else {
  rowData[10] = 0;
  rowData[11] = 0;
} // Column L: deltaH
    // ==================================================================================
  }

  sheet.appendRow(rowData);

  return ContentService.createTextOutput(JSON.stringify({"state": "success"}))
    .setMimeType(ContentService.MimeType.JSON);
}

function convertTimeToSeconds(timeStr) {
  if (!timeStr) return 0;
  var parts = String(timeStr).split(':');
  return (parseInt(parts[0]) * 3600) + (parseInt(parts[1]) * 60) + (parseInt(parts[2]) || 0);
}

function getCachedTout() {
  var scriptProperties = PropertiesService.getScriptProperties();
  
  var lastFetch = parseInt(scriptProperties.getProperty('LAST_WEATHER_FETCH')) || 0;
  var cachedTemp = scriptProperties.getProperty('CACHED_TOUT');
  var now = new Date().getTime();
  
  if (!cachedTemp || (now - lastFetch) > 900000) {
    try {
      var lat = "16.5432"; 
      var lon = "81.5224";
      var url = "https://api.open-meteo.com/v1/forecast?latitude=" + lat + "&longitude=" + lon + "&current=temperature_2m";
      
      var response = UrlFetchApp.fetch(url);
      var json = JSON.parse(response.getContentText());
      var currentTemp = json.current.temperature_2m;
      
      scriptProperties.setProperty('CACHED_TOUT', currentTemp);
      scriptProperties.setProperty('LAST_WEATHER_FETCH', now.toString());
      
      return parseFloat(currentTemp);
    } catch(err) {
      return cachedTemp ? parseFloat(cachedTemp) : 30.0;
    }
  }
  
  return parseFloat(cachedTemp);
}

function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu('Fix Permissions')
      .addItem('Authorize Weather API', 'testWeatherAPI')
      .addToUi();
}

function testWeatherAPI() {
  try {
    var url = "https://api.open-meteo.com/v1/forecast?latitude=16.5432&longitude=81.5224&current=temperature_2m";
    var response = UrlFetchApp.fetch(url, {muteHttpExceptions: true});
    Logger.log("Code: " + response.getResponseCode());
    Logger.log("Body: " + response.getContentText());
  } catch (err) {
    Logger.log("ERROR: " + err.message);
  }
}