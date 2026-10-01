# Railway par Deploy Karne Ka Tarika

## Is zip me kya hai
- `main.py` — tumhara bot code
- `yp_shop.db` — tumhara purana database (orders/users/balance) — pehle se isi zip me hai
- `requirements.txt` — zaroori Python libraries (aiogram, aiohttp)
- `Procfile` — Railway ko batata hai ki bot kaise start karna hai
- `runtime.txt` — Python version (3.11)
- `.gitignore` — database/log files ko GitHub par jaane se rokta hai
- `.env.example` — kaunse environment variables set karne hain, uski list

## Steps

1. **GitHub par upload karo**
   - Naya GitHub repo banao (private rakh sakte ho).
   - Is zip ke andar ki saari files (folder nahi, sirf files) us repo me upload/push karo.

2. **Railway par project banao**
   - https://railway.app par login karo.
   - "New Project" → "Deploy from GitHub repo" → apna repo select karo.

3. **Environment Variables set karo**
   - Project open karke "Variables" tab me jao.
   - `.env.example` file me di gayi saari cheezein add karo:
     - `BOT_TOKEN`
     - `ADMIN_ID`
     - `BOT_USERNAME`
     - `ADMIN_CONTACT`
   - Code me pehle se hardcoded token/ID hai, lekin agar tumne env variable set kiya to wahi use hoga — isliye apna asli bot token yaha DAALNA zaroori hai, purana token public ho chuka hai to naya token BotFather se leke daalo.

4. **Deploy**
   - Variables save karte hi Railway apne aap build + deploy start kar dega (Nixpacks `requirements.txt` aur `Procfile` ko dekh kar Python app detect kar lega).
   - "Deployments" tab me logs check karo — "CORE SYSTEM IS FULLY OPERATIONAL" jaisi line dikhni chahiye.

5. **Data persist karne ke liye (strongly recommended)**
   - `yp_shop.db` is zip me pehle se included hai (tumhara purana data), aur repo ke saath deploy ho jayegi.
   - Lekin Railway free/hobby plan par filesystem restart ke baad reset ho sakta hai — matlab wahi `yp_shop.db` delete ho sakti hai aur data khatam.
   - Isse bachne ke liye Railway me ek **Volume** attach karo (Settings → Volumes → mount path `/data`), phir first deploy se pehle `yp_shop.db` ko us Volume me upload/copy kar do (Railway ke shell/CLI se), aur Variables me:
     ```
     DB_PATH=/data/yp_shop.db
     ```
   - Ye add karo, taaki orders/users/balance data safe rahe aur restart par delete na ho.

6. **Bot test karo**
   - Telegram par apne bot ko `/start` bhejo — reply aana chahiye.

## Zaroori Warning
`main.py` ke andar tumhara Telegram bot token pehle se likha hua hai (hardcoded). Ye token kabhi public repo ya kisi aur ko mat bhejo — agar accidentally kisi ko mil gaya to BotFather se turant `/revoke` karke naya token generate kar lena, aur wahi naya token Railway ke `BOT_TOKEN` variable me daalna.
