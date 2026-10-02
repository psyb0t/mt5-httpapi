//+------------------------------------------------------------------+
//|                                             MT5ChartLoader.mq5     |
//|  Reference chart-deployment loader for mt5-httpapi chartctl.      |
//|                                                                   |
//|  Attach this to ANY single chart in the terminal (it doesn't      |
//|  matter which symbol/timeframe — it manages other charts, not     |
//|  its own). It reconciles the terminal to the desired deployments  |
//|  written by the API and reports live state back.                  |
//|                                                                   |
//|  It never trades. It only opens/closes charts and applies         |
//|  templates. Safe to run on a live account.                        |
//|                                                                   |
//|  This EA is intentionally thin: all logic lives in the portable   |
//|  include ChartControl.mqh so you can drop the same capability     |
//|  into your own resident EA (e.g. an account tracker) instead of   |
//|  running this standalone. See docs/chart-control-protocol.md.     |
//+------------------------------------------------------------------+
#property copyright "mt5-httpapi"
#property link      "https://github.com/psyb0t/mt5-httpapi"
#property version   "1.00"
#property strict

#include <ChartControl.mqh>

input int InpLoopSeconds = 1;   // reconcile timer period (seconds)

CChartControl ctl;

//+------------------------------------------------------------------+
int OnInit()
{
   // true: if another loader already owns the terminal, close this chart
   // and vanish (mt5start.ini [StartUp] re-attaches us on every launch;
   // this keeps that idempotent instead of accumulating loader charts).
   if(!ctl.Init(true))
      return INIT_FAILED;
   EventSetTimer(InpLoopSeconds < 1 ? 1 : InpLoopSeconds);
   Comment("MT5ChartLoader active — chartctl loader\n",
           ctl.IsOwner() ? "role: OWNER" : "role: passive (another loader owns the mutex)");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTimer()
{
   ctl.Tick();
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ctl.Deinit();
   Comment("");
}

//+------------------------------------------------------------------+
//| No trading. OnTick is intentionally empty; the loader is timer-  |
//| driven so it works on weekends and on disconnected symbols.      |
//+------------------------------------------------------------------+
void OnTick() {}
//+------------------------------------------------------------------+
