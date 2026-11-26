# 📈 Mutual Fund NAV Tracker & SIP Return Calculator

## 📌 Project Overview

The **Mutual Fund NAV Tracker & SIP Return Calculator** is a Python-based web application developed using Streamlit. It helps users track mutual fund Net Asset Value (NAV), monitor historical NAV trends, record SIP investments, and calculate investment returns using XIRR.

The application fetches historical NAV data directly from AMFI (Association of Mutual Funds in India) and stores it in an SQLite database for analysis and tracking.

## ✨ Features

- **Dashboard:** Displays the total number of mutual fund schemes, NAV records, and SIP transactions.
- **Download NAV:** Downloads historical mutual fund NAV data directly from AMFI.
- **Scheme Search:** Search mutual funds using scheme names or AMFI codes.
- **NAV History Chart:** Visualizes historical NAV trends with a 30-day moving average.
- **Add SIP Investment:** Record single investments or multiple monthly SIP instalments.
- **SIP Returns Calculator:** Calculates total invested amount, current portfolio value, profit/loss, and annualized XIRR.
- **Portfolio Analysis:** Displays investment growth, portfolio allocation, and scheme-wise returns.
- **Transaction Management:** View and delete recorded SIP transactions.
- **Data Storage:** Uses SQLite to store scheme details, NAV history, and SIP transactions.

## 🛠️ Technologies Used

| Technology | Purpose |
|---|---|
| Python | Core programming language |
| Streamlit | Web application interface |
| SQLite | Database management |
| Pandas | Data handling and analysis |
| Matplotlib | Data visualization |
| Requests | Fetching NAV data from AMFI |
| Datetime & Calendar | Date calculations and SIP scheduling |

## 📂 Project Structure

```text
Mutual-Fund-NAV-Tracker/
│
├── app.py                 # Main Streamlit application
├── mutual_fund.db         # SQLite database (created automatically)
├── requirements.txt       # Project dependencies
└── README.md              # Project documentation
```

## ⚙️ Installation and Setup

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/Mutual-Fund-NAV-Tracker.git
```

### 2. Navigate to the Project Folder

```bash
cd Mutual-Fund-NAV-Tracker
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Application

```bash
streamlit run app.py
```

The application will open in your default web browser.

## 📦 Requirements

Install the following Python libraries:

```text
streamlit
pandas
matplotlib
requests
```

SQLite, datetime, and calendar are included with Python and do not need separate installation.

## 📊 How to Use

1. Open the application and visit the Dashboard.
2. Navigate to **Download NAV** to import historical NAV data from AMFI.
3. Use **Scheme Search** to find mutual fund schemes.
4. Visit **NAV Chart** to analyze historical NAV movements.
5. Go to **Add SIP** to record investments.
6. Open **SIP Returns** to view portfolio performance, profit/loss, and XIRR.
7. Manage your investments using the transaction management section.

## 🗄️ Database Details

The application uses SQLite with three main tables:

- **schemes:** Stores mutual fund scheme codes and names.
- **nav_history:** Stores historical NAV values and dates.
- **sip_transactions:** Stores investment dates, amounts, and units purchased.

## 📈 Calculation Method

- **Units Purchased:** Investment Amount / NAV
- **Current Portfolio Value:** Total Units × Latest NAV
- **Profit/Loss:** Current Portfolio Value − Total Invested
- **XIRR:** Calculates annualized returns based on investment dates and cash flows.

## ⚠️ Disclaimer

This project is developed for educational and informational purposes only. The NAV data is sourced from AMFI. Investment calculations are estimates based on stored NAV data and recorded transactions.

This application does not provide financial advice or investment recommendations.

## 👩‍💻 Author

sameena

GitHub: https://github.com/your-username

---

⭐ If you find this project useful, consider giving it a star!
