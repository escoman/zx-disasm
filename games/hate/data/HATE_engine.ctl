; HATE_engine.ctl — постоянный движок
; база run10_56000.z80; код $CF32-$F2DF постоянен (3926/3926).
; Ввод: $E6FF по $C28F (0=KEYBOARD,1=SINCLAIR,2=KEMPSTON,3=PROTEK) -> $C290.
; Секреты в миссии: p = ABORT! (ветка $D0BA, подтверждение ENTER -> GAME OVER);
;   u+g = DEMO-режим ($D089->$D099, $F0DB=$FF, автопилот, зелёный рельеф).
; Меню SELECT: 1..4 = ввод, 5 = DEMO, 6 = START GAME (+SPACE гейт $F1D1).
; Графика: векторная, спрайт-шитов нет. Шрифт $6300 (96x8, MSB); окно $EE17
;   (бит7 кода = разрыв, $FF конец); растеризатор форм $D105 ($FF=конец
;   объекта, поток -> таблица смещений $C9FE); HUD-полосы $DE2C/$DECB
;   (blit AND, записи через буфер $DD66); рельеф процедурный ($6000-строка,
;   backbuffer $8400-$BFFF). Статичный банк форм/уровней $653A-$8008
;   (дифф run10/br5: 6863 Б инвариантны) -> hate/data/gfx/bank_653A.bin.
; TAP: блок6 INXS-краш + RLE $5B8C ($97 escape; см. gfx/GFX_NOTES.md).

b $4000 Screen / backbuffer / variables
c $CF32 code
b $D015 data
c $D050 code
b $D184 data
c $D19B code
b $D29C data
c $D2E3 code
b $D4DF data
c $D4E9 code
b $D5B2 data
c $D9D6 code
b $DB4B data
c $DB60 code
b $E258 data
c $E262 code
b $E392 data
c $E3BF code
b $E5B2 data
c $E5C9 code
b $E6F1 data
c $E6FF code
b $E847 data
c $E889 code
b $E903 data
c $E94F code
b $E972 data
c $E9C4 code
b $E9C6 data
c $E9CE code
b $EC00 data
c $EC0B code
b $EC30 data
c $EC3F code
b $EC51 data
c $EC59 code
b $EC6A data
c $ECAC code
b $ECB1 data
c $ECDC code
b $ECE2 data
c $ECF3 code
b $ED39 data
c $ED81 code
b $ED90 data
c $EDCA code
b $EF46 data
c $EFA5 code
b $F0D9 data
c $F1AA code
b $F2E0 data
b $F2E0 high
