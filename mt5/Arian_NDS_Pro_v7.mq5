//+------------------------------------------------------------------+
//|                                             Arian_NDS_Pro_v7.mq5 |
//|   ربات نوسانگیری فوق دقیق آریان NDS — نسخه ۷.۰ حرفهای            |
//|   مختص طلا (GOLD / XAUUSD) و بیتکوین (BITCOIN / BTCUSD)           |
//|                                                                  |
//|   ارتقاها و رفع باگهای نسخه ۶.۱:                                  |
//|   ۱. اضافه شدن فیلتر همسویی قطعی با روند ماژور (EMA 50 / 200)      |
// فیلتر روند؛ عملکرد باید روی دادهٔ معتبر ارزیابی شود.
//|      - معاملات لانگ فقط در روند صعودی و شورت فقط در روند نزولی.   |
//|   ۲. رفع باگ بسته شدن زودهنگام و منفی در TP:                     |
//|      - اعتبارسنجی دقیق فاصله TP و SL با اسپرد زنده و استاپلول بروکر|
//|      - جلوگیری قطعی از معکوس شدن یا تاچ آنی TP                     |
//|   ۳. مدیریت سرمایه با سقف ریسک ۰٫۲۵٪ (محاسبه خودکار لات سایز):              |
//|      - ریسک حداکثر ۰٫۲۵٪ بالانس حساب در هر پوزیشن       |
//|   ۴. سازگاری کامل با انواع بروکرها و حالت پر شدن سفارش (IOC/FOK)   |
//|   ۵. نوسانگیری سریع و متوالی در تایمفریم ۵ دقیقه (M5)              |
//+------------------------------------------------------------------+
#property copyright "Arian NDS Pro v7.0 - High Precision Scalper"
#property version   "7.31"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

// Stop-only file bridge, scoped by broker/account/magic, usable while ticks stop.
string DashboardFile()
{
   string server=AccountInfoString(ACCOUNT_SERVER),safe="";
   for(int i=0;i<StringLen(server);i++) {
      ushort c=StringGetCharacter(server,i);
      bool allowed=(c>=65&&c<=90)||(c>=97&&c<=122)||(c>=48&&c<=57)||c==45||c==95;
      safe+=allowed?StringSubstr(server,i,1):"_";
   }
   return "arian_control_"+safe+"_"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+"_"+IntegerToString(MagicNumber)+".txt";
}
double dashboardRisk=0;
string dashboardCommand="",dashboardState="NO_COMMAND",entryReason="WAIT_SETUP";
void EntryReason(string reason)
{
   if(entryReason!=reason) Print("ENTRY STATUS: ",reason);
   entryReason=reason;
}
void DashboardStatus()
{
   static ulong last=0;
   if(GetTickCount64()-last<2000) return;
   last=GetTickCount64();
   string name=DashboardFile()+"."+IntegerToString(ChartID())+".status";
   int h=FileOpen(name,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ);
   if(h==INVALID_HANDLE) return;
   bool latched=GlobalVariableCheck(AuditKey()) && GlobalVariableGet(AuditKey())>0;
   string state=dashboardState;
   if(state=="READY" && !EnableEntries) state="INPUT_DISABLED";
   if(state=="READY" && latched) state="EXECUTION_LATCH";
   if(state=="READY" && (!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))) state="ALGO_DISABLED";
   FileWrite(h,_Symbol+"|"+IntegerToString(ChartID())+"|7.31|"+dashboardCommand+"|"+state+"|"+(EnableEntries?"1":"0")+"|"+(latched?"1":"0")+"|"+IntegerToString(TimeGMT())+"|"+EnumToString(_Period)+"|"+entryReason);
   FileClose(h);
}
bool DashboardStop()
{
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO || AccountInfoString(ACCOUNT_CURRENCY)!="USD") return true;
   dashboardRisk=0;
   dashboardCommand="";dashboardState="NO_COMMAND";
   bool blocked=true,flatten=false;
   string file=DashboardFile();
   int handle=FileOpen(file,FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(handle!=INVALID_HANDLE) {
      string command=FileReadString(handle); FileClose(handle);
      string fields[];int count=StringSplit(command,'|',fields);
      dashboardCommand=count>0?fields[0]:"";
      flatten=StringFind(command,"FLATTEN:")==0;
      bool pause=StringFind(command,"PAUSE:")==0;
      if(flatten || pause) {
         dashboardState=flatten?"FLATTEN":"PAUSE";
         handle=FileOpen(file+".ack",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
         if(handle!=INVALID_HANDLE) {FileWrite(handle,command);FileClose(handle);}
      } else if(count==4 && StringFind(fields[0],"RUN:")==0) {
         long remaining=StringToInteger(fields[1])-(long)TimeGMT();
         double risk=StringToDouble(fields[2]);
         dashboardState="LEASE_EXPIRED";
         if(remaining>0 && remaining<=25 && risk>=0 && risk<=0.25 && (risk>0 || fields[3]=="0") && AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO) {
            dashboardRisk=risk;
            blocked=fields[3]!="1";
            dashboardState=blocked?"RISK_BLOCK":"READY";
         }
      }
   }
   DashboardStatus();
   bool latched=GlobalVariableCheck(AuditKey()) && GlobalVariableGet(AuditKey())>0;
   if(!blocked && EnableEntries && !latched) return false;
   static ulong attempted=0;
   ulong clock=GetTickCount64();
   if(attempted && clock-attempted<1000) return true;
   attempted=clock;
   for(int i=OrdersTotal()-1;i>=0;i--) {
      ulong ticket=OrderGetTicket(i);
      if(ticket>0 && OrderGetInteger(ORDER_MAGIC)==(long)MagicNumber && OrderGetInteger(ORDER_TYPE)>=ORDER_TYPE_BUY_LIMIT && (blocked || OrderGetString(ORDER_SYMBOL)==_Symbol))
         trade.OrderDelete(ticket);
   }
   if(flatten && AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO) {
      for(int i=PositionsTotal()-1;i>=0;i--) {
         ulong ticket=PositionGetTicket(i);
         if(ticket>0 && PositionGetInteger(POSITION_MAGIC)==(long)MagicNumber) {
            trade.SetTypeFillingBySymbol(PositionGetString(POSITION_SYMBOL));trade.PositionClose(ticket);
         }
      }
      trade.SetTypeFillingBySymbol(_Symbol);
   }
   return true;
}
void OnTimer() {DashboardStop();}

// Guarded build: entries require explicit activation after execution-feed review.
input bool EnableEntries = false;
input bool ResetExecutionAuditLatch = false;
string AuditKey() { return "AR:Audit:"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+":"+IntegerToString(MagicNumber)+":"+StringSubstr(_Symbol,0,12); }
string CycleKey() { return "AR:Cycle:"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+":"+IntegerToString(MagicNumber)+":"+StringSubstr(_Symbol,0,12); }
void OnTradeTransaction(const MqlTradeTransaction &trans,const MqlTradeRequest &request,const MqlTradeResult &result)
{
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD || !HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetInteger(trans.deal,DEAL_MAGIC)!=(long)MagicNumber || HistoryDealGetString(trans.deal,DEAL_SYMBOL)!=_Symbol) return;
   if(HistoryDealGetInteger(trans.deal,DEAL_ENTRY)!=DEAL_ENTRY_IN) return;
   MqlTick tick;
   long stamp=HistoryDealGetInteger(trans.deal,DEAL_TIME_MSC);
   double price=HistoryDealGetDouble(trans.deal,DEAL_PRICE);
   bool valid=SymbolInfoTick(_Symbol,tick) && tick.bid>0 && tick.ask>=tick.bid && MathAbs((double)(tick.time_msc-stamp))<=2000;
   double ref=0,tolerance=0;
   if(valid) {
      ref=HistoryDealGetInteger(trans.deal,DEAL_TYPE)==DEAL_TYPE_BUY?tick.ask:tick.bid;
      tolerance=MathMax(3*(tick.ask-tick.bid),MathMax(20*_Point,5*SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE)));
   }
   if(!valid || MathAbs(price-ref)>tolerance) {
      GlobalVariableSet(AuditKey(),1);
      PrintFormat("EXECUTION REVIEW: entries latched off. deal=%I64u fill=%.8f quote=%.8f tolerance=%.8f",trans.deal,price,ref,tolerance);
   }
}


//================== ورودیهای قابل تنظیم ==================
input group "=== تنظیمات سایکل و استراتژی NDS ==="
input int    HA_ScanBars           = 60;     // بازهی اسکن هیکنآشی (تعداد کندل)
input int    MinCorrectionCandles = 2;      // تعداد کندل همرنگ برای تایید اصلاح (۲ برای اسکلپ سریع، ۳ برای کلاسیک)
input double EntryFib             = 0.864;  // سطح ورود فیبوناچی (اصلاح عمیق 0.864 یا 0.786)
input double TP_Fib               = 0.600;  // سطح حد سود سریع (0.60 یا 0.65 برای تعیین هدف هندسی)
input double SL_Fib               = 1.272;  // سطح حد ضرر هندسی (فراتر از نقطه ۰)
input double MinCycleATRMult      = 0.8;    // حداقل دامنه سایکل نسبت به ATR (فیلتر نویز)

input group "=== فیلتر روند ماژور ==="
input bool   UseTrendFilter       = true;   // فعال بودن فیلتر روند ماژور
input int    FastEMA               = 20;     // دوره میانگین متحرک سریع
input int    SlowEMA               = 50;     // دوره میانگین متحرک کند

input group "=== مدیریت سرمایه و حجم معامله ==="
input bool   UseRiskPercent       = true;   // true = محاسبه خودکار لات بر اساس درصد ریسک
input double RiskPercent          = 0.25;    // درصد ریسک در هر معامله؛ سقف مستقل در لایهٔ ارسال برقرار است
input double FixedLotSize         = 0.05;   // حجم معامله ثابت (در صورت خاموش بودن درصد ریسک)
input int    MaxSpreadPoints      = 80;     // حداکثر اسپرد مجاز برای ورود (پوینت)
input int    PendingExpiryBars    = 25;     // انقضای سفارش معلق بر حسب تعداد کندل
input ulong  MagicNumber          = 777888; // کد جادویی ربات

//================== متغیرهای سراسری ==================
double   point0Price = 0, point1Price = 0;
datetime point0Time = 0, point1Time = 0;
bool     isLongSetup = false;
bool     cycleReady  = false;
datetime lastBarTime = 0;
datetime tradedCycleTime = 0;
int      handleEMA20, handleEMA50;

//+------------------------------------------------------------------+
//| تابع راهاندازی اکسپرت                                           |
//+------------------------------------------------------------------+
int OnInit()
{
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO) return INIT_FAILED;
   EventSetTimer(1);
   if(ResetExecutionAuditLatch) GlobalVariableDel(AuditKey());
   if(GlobalVariableCheck(CycleKey())) tradedCycleTime=(datetime)GlobalVariableGet(CycleKey());
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);

   // تنظیم خودکار حالت پر شدن سفارش بر اساس قوانین بروکر روی این نماد
   uint fillingMode = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((fillingMode & SYMBOL_FILLING_IOC) != 0)
      trade.SetTypeFilling(ORDER_FILLING_IOC);
   else if((fillingMode & SYMBOL_FILLING_FOK) != 0)
      trade.SetTypeFilling(ORDER_FILLING_FOK);
   else
      trade.SetTypeFilling(ORDER_FILLING_RETURN);

   handleEMA20 = iMA(_Symbol, _Period, FastEMA, 0, MODE_EMA, PRICE_CLOSE);
   handleEMA50 = iMA(_Symbol, _Period, SlowEMA, 0, MODE_EMA, PRICE_CLOSE);

   Print("✅ اکسپرت آریان NDS نسخه ۷ فعال شد روی نماد: ", _Symbol, " | تایمفریم: ", EnumToString(_Period), " | درصد ریسک: ", RiskPercent, "%");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| خاتمه کار اکسپرت                                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   FileDelete(DashboardFile()+"."+IntegerToString(ChartID())+".status",FILE_COMMON);
   IndicatorRelease(handleEMA20);
   IndicatorRelease(handleEMA50);
}

//+------------------------------------------------------------------+
//| بررسی جهت روند ماژور با EMA                                      |
//+------------------------------------------------------------------+
bool IsTrendAligned(bool isLong)
{
   if(!UseTrendFilter) return true;

   double ema20Val[], ema50Val[];
   ArraySetAsSeries(ema20Val, true);
   ArraySetAsSeries(ema50Val, true);

   if(CopyBuffer(handleEMA20, 0, 1, 2, ema20Val) < 2 || CopyBuffer(handleEMA50, 0, 1, 2, ema50Val) < 2)
      return false;

   double closePrice = iClose(_Symbol, _Period, 1);

   if(isLong)
   {
      // روند صعودی: قیمت بالای EMA 50 و ترجیحاً EMA 20 بالای EMA 50
      return (closePrice >= ema50Val[0] && ema20Val[0] >= ema50Val[0]);
   }
   else
   {
      // روند نزولی: قیمت زیر EMA 50 و ترجیحاً EMA 20 زیر EMA 50
      return (closePrice <= ema50Val[0] && ema20Val[0] <= ema50Val[0]);
   }
}

//+------------------------------------------------------------------+
//| محاسبه ATR برای سنجش نوسان                                      |
//+------------------------------------------------------------------+
double GetATR(int period = 14)
{
   double sum = 0;
   for(int i = 1; i <= period; i++)
   {
      double h = iHigh(_Symbol, _Period, i);
      double l = iLow(_Symbol, _Period, i);
      double prevC = iClose(_Symbol, _Period, i + 1);
      double tr = MathMax(h - l, MathMax(MathAbs(h - prevC), MathAbs(l - prevC)));
      sum += tr;
   }
   return (sum / period);
}

//+------------------------------------------------------------------+
//| پیدا کردن سایکل بر پایهی هیکنآشی                                |
//+------------------------------------------------------------------+
bool FindCycle(int nowIdx, int window, int minCandles,
               double &outP0, datetime &outT0,
               double &outP1, datetime &outT1,
               bool &outLong, int &outRunStartReal)
{
   int bars = iBars(_Symbol, _Period);
   if(nowIdx + window >= bars) window = bars - nowIdx - 1;
   if(window < minCandles + 2) return false;

   double haOpen[], haClose[];
   ArrayResize(haOpen, window);
   ArrayResize(haClose, window);

   for(int k = 0; k < window; k++)
   {
      int realIdx = nowIdx + window - k;
      double o = iOpen(_Symbol, _Period, realIdx);
      double c = iClose(_Symbol, _Period, realIdx);
      double h = iHigh(_Symbol, _Period, realIdx);
      double l = iLow(_Symbol, _Period, realIdx);
      double hc = (o + h + l + c) / 4.0;
      double ho = (k == 0) ? (o + c) / 2.0 : (haOpen[k - 1] + haClose[k - 1]) / 2.0;
      haOpen[k]  = ho;
      haClose[k] = hc;
   }

   int runColor = 0, runLength = 0, lastRunStartK = -1, lastRunColor = 0;
   for(int k = 0; k < window; k++)
   {
      int candleColor = (haClose[k] > haOpen[k]) ? 1 : -1;
      if(candleColor == runColor) runLength++;
      else { runColor = candleColor; runLength = 1; }
      if(runLength == minCandles)
      {
         lastRunStartK = k - minCandles + 1;
         lastRunColor  = runColor;
      }
   }
   if(lastRunStartK < 0) return false;

   int runStartReal = nowIdx + window - lastRunStartK;
   int prevReal = MathMin(runStartReal + 1, bars - 1);

   if(lastRunColor == -1)
   {
      // اصلاح قرمز هیکنآشی => نقطه ۰ سقف شروع حرکت
      outP0 = iHigh(_Symbol, _Period, prevReal);
      outT0 = iTime(_Symbol, _Period, prevReal);

      double lowestLow = DBL_MAX; int lowestIdx = runStartReal;
      for(int j = nowIdx; j <= runStartReal; j++)
      {
         double lo = iLow(_Symbol, _Period, j);
         if(lo < lowestLow) { lowestLow = lo; lowestIdx = j; }
      }
      outP1 = lowestLow;
      outT1 = iTime(_Symbol, _Period, lowestIdx);
      outLong = false;
   }
   else
   {
      // اصلاح سبز هیکنآشی => نقطه ۰ کف شروع حرکت
      outP0 = iLow(_Symbol, _Period, prevReal);
      outT0 = iTime(_Symbol, _Period, prevReal);

      double highestHigh = -DBL_MAX; int highestIdx = runStartReal;
      for(int j = nowIdx; j <= runStartReal; j++)
      {
         double hi = iHigh(_Symbol, _Period, j);
         if(hi > highestHigh) { highestHigh = hi; highestIdx = j; }
      }
      outP1 = highestHigh;
      outT1 = iTime(_Symbol, _Period, highestIdx);
      outLong = true;
   }

   outRunStartReal = runStartReal;
   return true;
}

//+------------------------------------------------------------------+
//| محاسبه سطوح فیبوناچی دقیق NDS                                    |
//+------------------------------------------------------------------+
void GetFibLevels(double &entryPrice, double &slPrice, double &tpPrice)
{
   double range = MathAbs(point1Price - point0Price);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   if(isLongSetup)
   {
      // در پوزیشن خرید: ورود پایینتر از نقطه ۱ (در اصلاح 0.864)
      entryPrice = NormalizeDouble(point1Price - range * EntryFib, digits);
      slPrice    = NormalizeDouble(point1Price - range * SL_Fib, digits);
      tpPrice    = NormalizeDouble(point1Price - range * TP_Fib, digits);
   }
   else
   {
      // در پوزیشن فروش: ورود بالاتر از نقطه ۱ (در اصلاح 0.864)
      entryPrice = NormalizeDouble(point1Price + range * EntryFib, digits);
      slPrice    = NormalizeDouble(point1Price + range * SL_Fib, digits);
      tpPrice    = NormalizeDouble(point1Price + range * TP_Fib, digits);
   }
}

//+------------------------------------------------------------------+
//| آپدیت سایکل و کنترل اعتبار آن                                    |
//+------------------------------------------------------------------+
void UpdateSwingPoints()
{
   double p0, p1; datetime t0, t1; bool longSetup; int runStartReal;
   if(!FindCycle(1, HA_ScanBars, MinCorrectionCandles, p0, t0, p1, t1, longSetup, runStartReal))
      return;

   double range = MathAbs(p1 - p0);
   double atr = GetATR(14);

   // فیلتر حداقل دامنه نوسان (جلوگیری از اسکلپ روی نویزهای ریز)
   if(range < atr * MinCycleATRMult)
      return;

   // فیلتر همسویی با روند ماژور
   if(!IsTrendAligned(longSetup))
      return;

   if(t1 != point1Time || t0 != point0Time)
   {
      CancelPendingOrder();

      point0Price = p0; point0Time = t0;
      point1Price = p1; point1Time = t1;
      isLongSetup = longSetup;
      cycleReady  = true;

      // چک اعتبارسنجی: آیا سایکل قبل از شناسایی ما به تارگت رسیده بود؟
      double entryPriceCheck, slPriceCheck, tpPriceCheck;
      GetFibLevels(entryPriceCheck, slPriceCheck, tpPriceCheck);
      bool entryTouched = false;
      bool playedOut = false;

      for(int k = runStartReal - 1; k >= 0; k--)
      {
         double hi = iHigh(_Symbol, _Period, k);
         double lo = iLow(_Symbol, _Period, k);
         if(!entryTouched)
         {
            if(isLongSetup && lo <= entryPriceCheck) entryTouched = true;
            if(!isLongSetup && hi >= entryPriceCheck) entryTouched = true;
         }
         else
         {
            if(isLongSetup && hi >= tpPriceCheck) { playedOut = true; break; }
            if(!isLongSetup && lo <= tpPriceCheck) { playedOut = true; break; }
         }
      }

      if(playedOut)
      {
         tradedCycleTime = point1Time;
         cycleReady = false;
         Print("⏭️ این سایکل قبلاً به تارگت رسیده بود و منقضی شد.");
         return;
      }

      Print("🎯 سایکل فعال تایید شد: ", (isLongSetup ? "خرید (LONG)" : "فروش (SHORT)"),
            " | Point0=", point0Price, " Point1=", point1Price, " | دامنه=", DoubleToString(range, 2));
   }
}

//+------------------------------------------------------------------+
//| محاسبه حجم لات دقیق بر اساس ریسک ۵٪ بالانس حساب                   |
//+------------------------------------------------------------------+
double CalcLotSize(double entryPrice,double slPrice)
{
   if(entryPrice<=0 || slPrice<=0 || entryPrice==slPrice) return 0;
   double loss=0;
   ENUM_ORDER_TYPE side=isLongSetup?ORDER_TYPE_BUY:ORDER_TYPE_SELL;
   if(!OrderCalcProfit(side,_Symbol,1.0,entryPrice,slPrice,loss) || loss>=0) return 0;
   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   if(step<=0 || minLot<=0 || maxLot<minLot) return 0;
   double budget=AccountInfoDouble(ACCOUNT_BALANCE)*MathMin(MathMin(RiskPercent,0.25),dashboardRisk)/100.0;
   double raw=UseRiskPercent?budget/(MathAbs(loss)+7.0):FixedLotSize;
   double lots=MathFloor((MathMin(raw,maxLot)+1e-12)/step)*step;
   if(lots<minLot || (lots*(MathAbs(loss)+7.0)>budget+1e-6)) return 0;
   return NormalizeDouble(lots,8);
}

//+------------------------------------------------------------------+
//| بررسی وجود معامله یا اردر باز متعلق به این اکسپرت                 |
//+------------------------------------------------------------------+
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetInteger(POSITION_MAGIC) == MagicNumber && PositionGetString(POSITION_SYMBOL) == _Symbol)
         return true;
   }
   return false;
}

bool HasPendingOrder()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0 && OrderGetInteger(ORDER_MAGIC) == MagicNumber && OrderGetString(ORDER_SYMBOL) == _Symbol)
         return true;
   }
   return false;
}

void CancelPendingOrder()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket <= 0) continue;
      if(OrderGetInteger(ORDER_MAGIC)==(long)MagicNumber && OrderGetString(ORDER_SYMBOL)==_Symbol && OrderGetInteger(ORDER_TYPE)>=ORDER_TYPE_BUY_LIMIT)
         trade.OrderDelete(ticket);
   }
}

//+------------------------------------------------------------------+
//| ارسال و ثبت سفارش معلق روی سطح 0.864                             |
//+------------------------------------------------------------------+
void PlaceOrderLocked()
{
   if(DashboardStop()) return;
   if(PositionsTotal()>0 || OrdersTotal()>0) {EntryReason("ACCOUNT_BUSY");return;}
   if(!EnableEntries || (GlobalVariableCheck(AuditKey()) && GlobalVariableGet(AuditKey())>0)) return;
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick) || tick.bid<=0 || tick.ask<tick.bid) {EntryReason("INVALID_QUOTE");return;}
   // OnTick TimeCurrent is the server timestamp of the received quote.
   // TimeTradeServer is extrapolated using the PC clock and can falsely reject fresh ticks.
   if(TimeCurrent()-tick.time<0 || TimeCurrent()-tick.time>15) {EntryReason("STALE_QUOTE");return;}
   double entryPrice,slPrice,tpPrice;
   GetFibLevels(entryPrice,slPrice,tpPrice);
   double grid=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   if(grid<=0) {EntryReason("INVALID_TICK_SIZE");return;}
   entryPrice=NormalizeDouble(MathRound(entryPrice/grid)*grid,_Digits);
   slPrice=NormalizeDouble((isLongSetup?MathFloor(slPrice/grid):MathCeil(slPrice/grid))*grid,_Digits);
   tpPrice=NormalizeDouble(MathRound(tpPrice/grid)*grid,_Digits);
   double spread=tick.ask-tick.bid;
   double minDistance=MathMax(grid,SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*_Point);
   if(spread/_Point>MaxSpreadPoints) {EntryReason("SPREAD_LIMIT");return;}
   // Strict pending-only entries. A passed entry never becomes a market order.
   if(isLongSetup) {
      if(entryPrice>=tick.ask-minDistance || slPrice>=entryPrice-minDistance || tpPrice<=entryPrice+minDistance+spread) {EntryReason("BUY_LIMIT_LEVELS");return;}
   } else {
      if(entryPrice<=tick.bid+minDistance || slPrice<=entryPrice+minDistance || tpPrice>=entryPrice-minDistance-spread) {EntryReason("SELL_LIMIT_LEVELS");return;}
   }
   double lots=CalcLotSize(entryPrice,slPrice);
   if(lots<=0) {EntryReason("VOLUME_BELOW_MIN_OR_RISK");return;}
   datetime expiry=TimeCurrent()+PeriodSeconds(_Period)*PendingExpiryBars;
   // Persist before submission: ambiguous responses must never retry this cycle.
   tradedCycleTime=point1Time;
   GlobalVariableSet(CycleKey(),(double)tradedCycleTime);
   bool submitted=isLongSetup?trade.BuyLimit(lots,entryPrice,_Symbol,slPrice,tpPrice,ORDER_TIME_SPECIFIED,expiry,"Arian-BuyLimit"):
                              trade.SellLimit(lots,entryPrice,_Symbol,slPrice,tpPrice,ORDER_TIME_SPECIFIED,expiry,"Arian-SellLimit");
   uint retcode=trade.ResultRetcode();
   if(submitted && trade.ResultOrder()>0 && (retcode==TRADE_RETCODE_DONE || retcode==TRADE_RETCODE_PLACED)) {
      tradedCycleTime=point1Time;
      GlobalVariableSet(CycleKey(),(double)tradedCycleTime);
      EntryReason("PENDING_ACCEPTED");
      PrintFormat("PENDING ACCEPTED: ticket=%I64u Bid=%.8f Ask=%.8f Entry=%.8f SL=%.8f TP=%.8f",trade.ResultOrder(),tick.bid,tick.ask,entryPrice,slPrice,tpPrice);
   } else {
      EntryReason("BROKER_REJECT_"+IntegerToString(retcode));
      PrintFormat("PENDING REJECTED: retcode=%u %s",retcode,trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| حلقه تیک زنده بازار                                              |
//+------------------------------------------------------------------+
void PlaceOrder()
{
   int lock=FileOpen(DashboardFile()+".entry.lock",FILE_READ|FILE_WRITE|FILE_BIN|FILE_COMMON);
   if(lock==INVALID_HANDLE) {EntryReason("ENTRY_LOCK_BUSY");return;}
   PlaceOrderLocked();
   FileClose(lock);
}

void OnTick()
{
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)!=ACCOUNT_TRADE_MODE_DEMO || DashboardStop()) return;
   if(!EnableEntries || (GlobalVariableCheck(AuditKey()) && GlobalVariableGet(AuditKey())>0)) return;
   // اگر پوزیشن بازی وجود دارد، سفارش معلق اضافه را حذف کن و منتظر خروج باش
   if(HasOpenPosition())
   {
      CancelPendingOrder();
      return;
   }

   // در هر کندل جدید تایمفریم، سایکلها را بهروزرسانی کن
   datetime currentBarTime = iTime(_Symbol, _Period, 0);
   if(currentBarTime != lastBarTime)
   {
      lastBarTime = currentBarTime;
      UpdateSwingPoints();
   }

   if(!cycleReady) {EntryReason("WAIT_SETUP");return;}
   if(HasPendingOrder()) {EntryReason("PENDING_ACTIVE");return;}

   // اگر این سایکل قبلاً معامله شده، منتظر سایکل بعدی بمان
   if(point1Time == tradedCycleTime) {EntryReason("CYCLE_ALREADY_USED");return;}

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   // باطل شدن سایکل در صورت فراتر رفتن قیمت از نقطه ۱
   if(isLongSetup && ask > point1Price)
   {
      cycleReady = false;
      CancelPendingOrder();
      return;
   }
   if(!isLongSetup && bid < point1Price)
   {
      cycleReady = false;
      CancelPendingOrder();
      return;
   }

   PlaceOrder();
}
//+------------------------------------------------------------------+
