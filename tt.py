import streamlit as st
import sqlite3
import pandas as pd
import json
import google.generativeai as genai
import random

# ========================
# CONFIG
# ========================
AI_PROMPT_TEMPLATE = """
You are a timetable generator for a school. Follow these rules strictly:
1. No teacher overlaps across grades/sections at the same time.
2. No section gets the same subject more than 2 times/day, unless the section attends only on certain days.
3. Each section and each teacher must have at least one 'Games' period/week.
4. If a teacher is absent for a period, replace them with another teacher of the same subject. If none available, put 'Games'.
5. A subject for a section is always taught by only one teacher.
6. Distribute periods evenly across available days for the section.
Return output as JSON: { "timetable": { "GRADE-SECTION": { "Monday": ["subj-teacher", ...], "Tuesday": [...], ... } } }
"""

DB_FILE = "timetable.db"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# ========================
# DB INIT
# ========================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        subject TEXT,
        grades TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        grade TEXT,
        sections TEXT,
        periods_per_week INTEGER
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS timetable (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grade TEXT,
        section TEXT,
        day TEXT,
        period INTEGER,
        subject TEXT,
        teacher TEXT
    )""")
    conn.commit()
    conn.close()

def get_connection():
    return sqlite3.connect(DB_FILE)

# ========================
# AI GENERATION
# ========================
def generate_ai_timetable(absentees):
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM teachers")
    teachers = cur.fetchall()
    cur.execute("SELECT * FROM subjects")
    subjects = cur.fetchall()
    conn.close()

    data = {
        "teachers": [{"name": t[1], "subject": t[2], "grades": t[3]} for t in teachers],
        "subjects": [{"name": s[1], "grade": s[2], "sections": json.loads(s[3]), "periods_per_week": s[4]} for s in subjects],
        "absentees": absentees
    }

    model = genai.GenerativeModel("gemini-pro")
    prompt = AI_PROMPT_TEMPLATE + "\nHere is the school data:\n" + json.dumps(data)
    response = model.generate_content(prompt)
    timetable_json = json.loads(response.text)

    save_timetable(timetable_json["timetable"])

def save_timetable(timetable_dict):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM timetable")
    for grade_section, days in timetable_dict.items():
        grade, section = grade_section.split("-")
        for day, periods in days.items():
            for i, val in enumerate(periods, start=1):
                if "-" in val:
                    subject, teacher = val.split("-", 1)
                else:
                    subject, teacher = val, ""
                cur.execute("INSERT INTO timetable (grade, section, day, period, subject, teacher) VALUES (?, ?, ?, ?, ?, ?)",
                            (grade, section, day, i, subject, teacher))
    conn.commit()
    conn.close()

# ========================
# UI
# ========================
st.set_page_config(page_title="School Timetable", layout="wide")
init_db()
tabs = st.tabs(["📥 Setup", "🚫 Absentees", "📅 Timetable"])

# Setup
with tabs[0]:
    st.header("Add Teachers")
    with st.form("add_teacher"):
        name = st.text_input("Teacher Name")
        subject = st.text_input("Subject")
        grades = st.text_input("Grades (comma-separated)")
        if st.form_submit_button("Add Teacher") and name and subject:
            conn = get_connection()
            conn.execute("INSERT INTO teachers (name, subject, grades) VALUES (?, ?, ?)", (name, subject, grades))
            conn.commit()
            conn.close()
            st.success("Teacher added")

    st.header("Add Subjects")
    with st.form("add_subject"):
        sub_name = st.text_input("Subject Name")
        grade = st.text_input("Grade")
        sections = st.text_input("Sections (comma-separated)")
        periods = st.number_input("Periods per week", min_value=1, max_value=14)
        if st.form_submit_button("Add Subject") and sub_name and grade:
            conn = get_connection()
            conn.execute("INSERT INTO subjects (name, grade, sections, periods_per_week) VALUES (?, ?, ?, ?)",
                         (sub_name, grade, json.dumps(sections.split(",")), periods))
            conn.commit()
            conn.close()
            st.success("Subject added")

# Absentees
absentees = {}
with tabs[1]:
    st.header("Mark Absent Teachers")
    conn = get_connection()
    teachers_list = [t[0] for t in conn.execute("SELECT name FROM teachers").fetchall()]
    conn.close()
    for day in WEEKDAYS:
        absentees[day] = st.multiselect(f"{day} Absentees", teachers_list, key=f"abs_{day}")

# Timetable
with tabs[2]:
    st.header("Timetable Viewer & Editor")
    conn = get_connection()
    grades_sections = conn.execute("SELECT DISTINCT grade, section FROM timetable").fetchall()
    conn.close()

    if grades_sections:
        selected_gs = st.selectbox("Select Grade-Section", [f"{g}-{s}" for g, s in grades_sections], key="sel_gs")
        col1, col2 = st.columns([1,1])
        with col1:
            if st.button("Generate with AI"):
                generate_ai_timetable(absentees)
                st.experimental_rerun()
        with col2:
            if st.button("Reset to AI Suggestion"):
                generate_ai_timetable(absentees)
                st.experimental_rerun()

        # Display timetable
        conn = get_connection()
        df = pd.read_sql_query("SELECT day, period, subject, teacher FROM timetable WHERE grade=? AND section=?",
                               conn, params=selected_gs.split("-"))
        conn.close()

        edit_mode = st.checkbox("Edit Timetable")
        timetable_grid = {day: [""] * 8 for day in WEEKDAYS}
        for _, row in df.iterrows():
            timetable_grid[row["day"]][row["period"] - 1] = f"{row['subject']}-{row['teacher']}"

        if edit_mode:
            conn = get_connection()
            teacher_names = [t[0] for t in conn.execute("SELECT name FROM teachers").fetchall()]
            subject_names = [s[0] for s in conn.execute("SELECT name FROM subjects").fetchall()]
            conn.close()
            for day in WEEKDAYS:
                st.subheader(day)
                cols = st.columns(8)
                for i in range(8):
                    subj = st.selectbox(f"{day} P{i+1} Subject", [""] + subject_names, key=f"{day}_{i}_subj", index=(subject_names.index(timetable_grid[day][i].split("-")[0]) + 1) if timetable_grid[day][i] else 0)
                    teach = st.selectbox(f"{day} P{i+1} Teacher", [""] + teacher_names, key=f"{day}_{i}_teach", index=(teacher_names.index(timetable_grid[day][i].split("-")[1]) + 1) if timetable_grid[day][i] and "-" in timetable_grid[day][i] else 0)
                    timetable_grid[day][i] = f"{subj}-{teach}" if subj else ""
            if st.button("Save Changes"):
                save_timetable({selected_gs: {day: timetable_grid[day] for day in WEEKDAYS}})
                st.success("Timetable updated")
        else:
            for day in WEEKDAYS:
                st.subheader(day)
                cols = st.columns(8)
                for i in range(8):
                    val = timetable_grid[day][i]
                    if val.startswith("Games"):
                        cols[i].markdown(f"<div style='background-color:lightgreen;padding:8px;border-radius:5px;text-align:center;'>{val}</div>", unsafe_allow_html=True)
                    else:
                        cols[i].markdown(f"<div style='background-color:#f0f0f0;padding:8px;border-radius:5px;text-align:center;'>{val}</div>", unsafe_allow_html=True)
    else:
        st.info("No timetable found. Add teachers/subjects and generate using AI.")
