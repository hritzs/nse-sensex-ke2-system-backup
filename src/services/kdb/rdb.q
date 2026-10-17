/ rdb.q - Real-Time Database (In-Memory)

/ The upd function is called via IPC by the Tickerplant to insert new rows
upd: insert;

/ Connect to the Tickerplant via Docker DNS (dalal_kdb, port 5000)
show "Connecting to Tickerplant...";
tp: hopen `:dalal_kdb:5000;

/ Subscribe to tables and retrieve their schemas
trade_schema: tp "sub[`trade]";
trade: trade_schema[1];

quote_schema: tp "sub[`quote]";
quote: quote_schema[1];

show "--- RDB Initialized & Subscribed to Tickerplant ---";
