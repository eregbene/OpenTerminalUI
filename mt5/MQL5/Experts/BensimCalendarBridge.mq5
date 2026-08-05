// BensimCalendarBridge.mq5
// Read-only MT5 Economic Calendar exporter. Attach to a demo chart when the
// MetaTrader5 Python package cannot expose calendar functions directly.
#property strict

input int LookaheadDays = 14;
input int HistoryDays = 30;
input int RefreshSeconds = 60;
input string OutputFile = "BensimCalendarBridge.json";

int OnInit()
{
   EventSetTimer(MathMax(10, RefreshSeconds));
   ExportCalendar();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   ExportCalendar();
}

void ExportCalendar()
{
   datetime from_time = TimeTradeServer() - HistoryDays * 86400;
   datetime to_time = TimeTradeServer() + LookaheadDays * 86400;
   MqlCalendarValue values[];
   int count = CalendarValueHistory(values, from_time, to_time);
   int handle = FileOpen(OutputFile, FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return;

   FileWriteString(handle, "{\"provider\":\"mt5_calendar\",\"mode\":\"READ_ONLY\",\"server_time\":" + IntegerToString((long)TimeTradeServer()) + ",\"received_at\":" + IntegerToString((long)TimeGMT()) + ",\"items\":[");
   for(int i = 0; i < count; i++)
   {
      MqlCalendarEvent event;
      MqlCalendarCountry country;
      bool has_event = CalendarEventById(values[i].event_id, event);
      bool has_country = has_event && CalendarCountryById(event.country_id, country);
      if(i > 0)
         FileWriteString(handle, ",");
      FileWriteString(handle, "{");
      FileWriteString(handle, "\"provider_event_id\":" + IntegerToString((long)values[i].event_id));
      FileWriteString(handle, ",\"provider_value_id\":" + IntegerToString((long)values[i].id));
      FileWriteString(handle, ",\"event_name\":\"" + JsonEscape(has_event ? event.name : "") + "\"");
      FileWriteString(handle, ",\"event_code\":\"" + JsonEscape(has_event ? event.event_code : "") + "\"");
      FileWriteString(handle, ",\"country\":\"" + JsonEscape(has_country ? country.name : "") + "\"");
      FileWriteString(handle, ",\"country_code\":\"" + JsonEscape(has_country ? country.code : "") + "\"");
      FileWriteString(handle, ",\"currency\":\"" + JsonEscape(has_country ? country.currency : "") + "\"");
      FileWriteString(handle, ",\"event_category\":\"" + JsonEscape(has_event ? EnumToString(event.type) : "") + "\"");
      FileWriteString(handle, ",\"importance\":\"" + JsonEscape(has_event ? EnumToString(event.importance) : "unknown") + "\"");
      FileWriteString(handle, ",\"scheduled_at_utc\":" + IntegerToString((long)values[i].time));
      FileWriteString(handle, ",\"period\":" + IntegerToString((long)values[i].period));
      FileWriteString(handle, ",\"actual_value\":" + IntegerToString((long)values[i].actual_value));
      FileWriteString(handle, ",\"forecast_value\":" + IntegerToString((long)values[i].forecast_value));
      FileWriteString(handle, ",\"previous_value\":" + IntegerToString((long)values[i].prev_value));
      FileWriteString(handle, ",\"revised_previous_value\":" + IntegerToString((long)values[i].revised_prev_value));
      FileWriteString(handle, ",\"status\":\"unknown\"");
      FileWriteString(handle, "}");
   }
   FileWriteString(handle, "]}");
   FileClose(handle);
}

string JsonEscape(string value)
{
   StringReplace(value, "\\", "\\\\");
   StringReplace(value, "\"", "\\\"");
   StringReplace(value, "\r", " ");
   StringReplace(value, "\n", " ");
   return value;
}
