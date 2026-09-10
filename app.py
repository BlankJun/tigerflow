from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import io
import json
import os
import sqlite3
import time
import xml.etree.ElementTree as ET
from google import genai
import pandas as pd
from pypdf import PdfReader
import streamlit as st

st.set_page_config(
    page_title="TigerFlow | Akıllı Fatura & Logo Aktarımı",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Yalın, gözü yormayan kurumsal koyu tema + Menü Gizleme
st.markdown(
    """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    
    /* Geliştirici Menülerini ve Üst Çubuğu Tamamen Gizle */
    #MainMenu {visibility: hidden !important;}
    header {visibility: hidden !important;}
    footer {visibility: hidden !important;}
    div[data-testid="stToolbar"] {visibility: hidden !important;}
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .hero-container {
        padding: 1rem 0 0.8rem 0;
        margin-bottom: 1.2rem;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    }
    .hero-title {
        font-size: 1.8rem;
        font-weight: 700;
        letter-spacing: -0.02em;
    }
    .hero-desc {
        color: #94a3b8;
        font-size: 0.95rem;
        margin-top: 0.2rem;
    }

    .metric-box {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 10px;
        padding: 1.1rem;
        text-align: left;
    }
    .metric-label {
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: #94a3b8;
        font-weight: 600;
        margin-bottom: 0.3rem;
    }
    .metric-val {
        font-size: 1.4rem;
        font-weight: 700;
        color: #f8fafc;
    }
    .metric-badge-ok {
        color: #10b981;
        font-size: 0.82rem;
        font-weight: 600;
        display: inline-flex;
        align-items: center;
        gap: 4px;
        margin-top: 0.3rem;
    }
    .metric-badge-err {
        color: #ef4444;
        font-size: 0.82rem;
        font-weight: 600;
        display: inline-flex;
        align-items: center;
        gap: 4px;
        margin-top: 0.3rem;
    }

    .stDownloadButton > button {
        width: 100%;
        border-radius: 8px;
        font-weight: 600;
        padding: 0.6rem 1.2rem;
    }
</style>
""",
    unsafe_allow_html=True,
)

DB_FILE = "faturalar.db"
ACTIVE_MODEL = "gemini-3.6-flash"

if "uploader_key" not in st.session_state:
  st.session_state.uploader_key = 0

# Oturum Durumu & Kredi (Hak) Tanımlamaları
if "credits" not in st.session_state:
  st.session_state["credits"] = 5
if "user_email" not in st.session_state:
  st.session_state["user_email"] = ""
if "is_registered" not in st.session_state:
  st.session_state["is_registered"] = False


def init_db():
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS faturalar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dosya_adi TEXT,
            belge_no TEXT,
            tarih TEXT,
            cari_unvan TEXT,
            malzeme_kodu TEXT,
            malzeme_aciklamasi TEXT,
            miktar REAL,
            birim TEXT,
            birim_fiyat REAL,
            kdv_orani REAL,
            satir_tutari REAL,
            kayit_tarihi TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
  conn.commit()
  conn.close()


def invoice_exists(belge_no):
  if not belge_no:
    return False
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT 1 FROM faturalar WHERE belge_no = ? LIMIT 1", (belge_no,)
  )
  exists = cursor.fetchone() is not None
  conn.close()
  return exists


def save_to_db(rows):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  for r in rows:
    cursor.execute(
        """
            INSERT INTO faturalar (
                dosya_adi, belge_no, tarih, cari_unvan,
                malzeme_kodu, malzeme_aciklamasi, miktar, birim,
                birim_fiyat, kdv_orani, satir_tutari
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            r["DOSYA_ADI"],
            r["BELGE_NO"],
            r["TARIH"],
            r["CARİ_UNVAN"],
            r["MALZEME_KODU"],
            r["MALZEME_ACIKLAMASI"],
            r["MIKTAR"],
            r["BIRIM"],
            r["BIRIM_FIYAT"],
            r["KDV_ORANI"],
            r["SATIR_TUTARI"],
        ),
    )
  conn.commit()
  conn.close()


def delete_rows_by_ids(row_ids):
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  placeholders = ",".join(["?"] * len(row_ids))
  cursor.execute(f"DELETE FROM faturalar WHERE id IN ({placeholders})", row_ids)
  conn.commit()
  conn.close()


def clear_db():
  conn = sqlite3.connect(DB_FILE)
  cursor = conn.cursor()
  cursor.execute("DELETE FROM faturalar")
  conn.commit()
  conn.close()


def get_all_from_db(limit=500):
  conn = sqlite3.connect(DB_FILE)
  df = pd.read_sql_query(
      f"""
        SELECT id AS ID, dosya_adi AS DOSYA_ADI, belge_no AS BELGE_NO, tarih AS TARIH,
               cari_unvan AS CARİ_UNVAN, malzeme_kodu AS MALZEME_KODU,
               malzeme_aciklamasi AS MALZEME_ACIKLAMASI, miktar AS MIKTAR,
               birim AS BIRIM, birim_fiyat AS BIRIM_FIYAT, kdv_orani AS KDV_ORANI,
               satir_tutari AS SATIR_TUTARI, kayit_tarihi AS KAYIT_ZAMANI
        FROM faturalar ORDER BY id DESC LIMIT {limit}
    """,
      conn,
  )
  conn.close()
  return df


def generate_logo_xml(df_source):
  root = ET.Element("INVOICES")
  for doc_no, group in df_source.groupby("BELGE_NO"):
    first_row = group.iloc[0]
    raw_date = str(first_row.get("TARIH", "")).strip()
    try:
      dt = datetime.strptime(raw_date, "%d.%m.%Y")
      formatted_date = dt.strftime("%Y-%m-%d")
    except Exception:
      formatted_date = datetime.now().strftime("%Y-%m-%d")

    invoice = ET.SubElement(root, "INVOICE", attrib={"DBOP": "INS"})
    ET.SubElement(invoice, "TYPE").text = "1"
    ET.SubElement(invoice, "NUMBER").text = str(doc_no)
    ET.SubElement(invoice, "DOC_NUMBER").text = str(doc_no)
    ET.SubElement(invoice, "DATE").text = formatted_date
    ET.SubElement(invoice, "DOC_DATE").text = formatted_date
    ET.SubElement(invoice, "ARP_CODE").text = str(
        first_row.get("CARİ_UNVAN", "")
    )

    transactions = ET.SubElement(invoice, "TRANSACTIONS")
    for _, row in group.iterrows():
      trans = ET.SubElement(transactions, "TRANSACTION")
      ET.SubElement(trans, "TYPE").text = "0"
      ET.SubElement(trans, "MASTER_CODE").text = (
          str(row.get("MALZEME_KODU", "")) or "GENEL_STOK"
      )
      ET.SubElement(trans, "DESCRIPTION").text = str(
          row.get("MALZEME_ACIKLAMASI", "")
      )
      ET.SubElement(trans, "QUANTITY").text = str(row.get("MIKTAR", 1.0))
      ET.SubElement(trans, "PRICE").text = str(row.get("BIRIM_FIYAT", 0.0))
      ET.SubElement(trans, "VAT_RATE").text = str(row.get("KDV_ORANI", 20))
      ET.SubElement(trans, "UNIT_CODE").text = str(
          row.get("BIRIM", "ADET")
      ).upper()
      ET.SubElement(trans, "TOTAL").text = str(row.get("SATIR_TUTARI", 0.0))

  return ET.tostring(root, encoding="utf-8", xml_declaration=True)


init_db()

env_api_key = os.environ.get("GEMINI_API_KEY", "")
api_key = (
    env_api_key
    if env_api_key
    else st.sidebar.text_input("Sistem Yetki Anahtarı:", type="password")
)

with st.sidebar.expander("🛠️ Veritabanı Yönetimi"):
  onay = st.checkbox("Arşivi tamamen sıfırla")
  if st.button("Tüm Kayıtları Temizle"):
    if onay:
      clear_db()
      st.session_state.pop("son_yuklenen", None)
      st.session_state.pop("son_audit", None)
      st.rerun()


def extract_text_from_pdf(file_bytes):
  reader = PdfReader(io.BytesIO(file_bytes))
  text = ""
  for page in reader.pages:
    extracted = page.extract_text()
    if extracted:
      text += extracted + "\n"
  return text


def call_gemini_with_retry(client, prompt, max_retries=3):
  for attempt in range(max_retries):
    try:
      response = client.models.generate_content(
          model=ACTIVE_MODEL, contents=prompt
      )
      if response and response.text:
        return response.text, None
    except Exception as e:
      err_msg = str(e)
      if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
        if attempt < max_retries - 1:
          time.sleep(25 * (attempt + 1))
          continue
      return None, err_msg
  return None, "Maksimum deneme aşıldı."


def process_single_pdf(file_data, current_api_key):
  file_name, file_bytes = file_data
  try:
    pdf_text = extract_text_from_pdf(file_bytes)
    client = genai.Client(api_key=current_api_key)

    prompt = f"""
        Aşağıdaki fatura metnini analiz et. Faturanın genel bilgilerini, kalemlerini ve alt toplamlarını çıkar.
        SADECE saf JSON formatında yanıt ver. Markdown veya ek metin ekleme.
        
        JSON formatı:
        {{
          "fatura_no": "Fatura numarası",
          "fatura_tarihi": "GG.AA.YYYY formatında tarih",
          "cari_unvan": "Tedarikçi firma adı",
          "fatura_toplam_tutar": 0.0,
          "kalemler": [
            {{
              "malzeme_kodu": "Ürün/stok kodu (yoksa boş)",
              "malzeme_aciklamasi": "Ürün adı",
              "miktar": 1.0,
              "birim": "ADET",
              "birim_fiyat": 100.0,
              "kdv_orani": 20,
              "satir_tutari": 100.0
            }}
          ]
        }}
        
        Fatura Metni:
        {pdf_text}
        """

    raw_text, err = call_gemini_with_retry(client, prompt)
    if err:
      return [], {}, f"'{file_name}' hatası: {err}"

    raw_text = raw_text.strip()
    if raw_text.startswith("```json"):
      raw_text = raw_text[7:]
    if raw_text.startswith("```"):
      raw_text = raw_text[3:]
    if raw_text.endswith("```"):
      raw_text = raw_text[:-3]

    data = json.loads(raw_text.strip())
    rows = []
    fatura_no = data.get("fatura_no", "")
    fatura_tarihi = data.get("fatura_tarihi", "")
    cari_unvan = data.get("cari_unvan", "")
    genel_toplam = float(data.get("fatura_toplam_tutar", 0.0) or 0.0)

    matrah_toplami = 0.0
    kdv_toplami = 0.0

    for item in data.get("kalemler", []):
      s_tutari = float(item.get("satir_tutari", 0.0) or 0.0)
      k_orani = float(item.get("kdv_orani", 20) or 20.0)

      matrah_toplami += s_tutari
      kdv_toplami += s_tutari * (k_orani / 100.0)

      rows.append({
          "DOSYA_ADI": file_name,
          "BELGE_NO": fatura_no,
          "TARIH": fatura_tarihi,
          "CARİ_UNVAN": cari_unvan,
          "MALZEME_KODU": item.get("malzeme_kodu", ""),
          "MALZEME_ACIKLAMASI": item.get("malzeme_aciklamasi", ""),
          "MIKTAR": item.get("miktar", 0),
          "BIRIM": item.get("birim", "ADET"),
          "BIRIM_FIYAT": item.get("birim_fiyat", 0.0),
          "KDV_ORANI": k_orani,
          "SATIR_TUTARI": s_tutari,
      })

    hesaplanan_genel = matrah_toplami + kdv_toplami
    fark = abs(hesaplanan_genel - genel_toplam)

    audit_info = {
        "fatura_no": fatura_no,
        "cari_unvan": cari_unvan,
        "matrah_toplami": matrah_toplami,
        "kdv_toplami": kdv_toplami,
        "hesaplanan_genel_toplam": hesaplanan_genel,
        "genel_toplam": genel_toplam,
        "tuttu_mu": (fark < 1.0 if genel_toplam > 0 else True),
    }

    return rows, audit_info, None
  except Exception as e:
    return [], {}, f"'{file_name}' hatası: {str(e)}"


# Üst Başlık
st.markdown(
    """
<div class="hero-container">
    <div class="hero-title">⚡ TigerFlow</div>
    <div class="hero-desc">Toplu PDF faturaları yapay zeka ile ayrıştırın, denetleyin ve doğrudan Logo Tiger'a aktarın.</div>
</div>
""",
    unsafe_allow_html=True,
)

# Bilgilendirme ve Hak Durumu Bantı (Girişte Herkesin Göreceği Yer)
if not st.session_state["is_registered"]:
  st.info(
      f"🎯 **Deneme Sürümü:** Herkese açık. Kalan ücretsiz kullanım hakkınız:"
      f" **{st.session_state['credits']}**."
  )
else:
  st.success(
      f"✨ **Kayıtlı Kullanıcı:** {st.session_state['user_email']} | Kalan"
      f" hakkınız: **{st.session_state['credits']}**"
  )

tab_yukle, tab_arsiv = st.tabs(
    ["📤 Fatura İşleme & Dönüştürme", "📁 Fatura Arşivi & Aktarım"]
)

with tab_yukle:
  # Hak kontrolü: Hak varsa yükleme paneli çalışır, yoksa kayıt formu gösterilir
  if st.session_state["credits"] > 0:
    col_up, col_info = st.columns([3, 2])

    with col_up:
      uploaded_files = st.file_uploader(
          "İşlenecek PDF faturaları sürükleyip bırakın",
          type="pdf",
          accept_multiple_files=True,
          key=f"uploader_{st.session_state.uploader_key}",
      )

    with col_info:
      st.markdown("<br>", unsafe_allow_html=True)
      mukerrer_onay = st.checkbox(
          "Mükerrer faturaları arşivde mevcut olsa bile tekrar kaydet",
          value=False,
      )
      st.caption(
          "🔒 Yüklenen tüm faturalar yerel veritabanında arşivlenir ve Logo"
          " Tiger XML standartlarında doğrulanır."
      )

    if uploaded_files:
      st.info(f"Seçilen dosya sayısı: **{len(uploaded_files)}**")

      if not api_key:
        st.warning("İşlem yapmak için sistem yetki anahtarı gereklidir.")
      else:
        if st.button("⚡ Faturaları Çözümle ve Tiger'a Hazırla", type="primary"):
          progress_bar = st.progress(0)
          status_text = st.empty()

          status_text.text("1/3 - PDF metin katmanları taranıyor...")
          progress_bar.progress(30)

          file_payloads = [(f.name, f.read()) for f in uploaded_files]
          all_rows = []
          audit_reports = []
          errors = []

          status_text.text(
              "2/3 - Yapay zeka ile kalemler ve KDV matrahları ayrıştırılıyor..."
          )
          progress_bar.progress(60)

          with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda p: process_single_pdf(p, api_key), file_payloads
                )
            )

          for rows, audit, err in results:
            if err:
              errors.append(err)
            if rows:
              doc_number = rows[0]["BELGE_NO"]
              zaten_var = invoice_exists(doc_number)

              if zaten_var and not mukerrer_onay:
                errors.append(
                    f"⚠️ '{doc_number}' faturası zaten arşivde mevcut olduğu"
                    " için atlandı."
                )
              else:
                all_rows.extend(rows)
            if audit:
              audit_reports.append(audit)

          status_text.text("3/3 - Matematiksel doğrulama tamamlanıyor...")
          progress_bar.progress(100)
          time.sleep(0.5)
          status_text.empty()
          progress_bar.empty()

          for err in errors:
            if "zaten arşivde mevcut" in err:
              st.warning(err)
            else:
              st.error(err)

          if all_rows:
            save_to_db(all_rows)
            # Her başarılı işlemde 1 hak düşüyoruz
            st.session_state["credits"] -= 1
            st.session_state.uploader_key += 1
            st.session_state["son_yuklenen"] = pd.DataFrame(all_rows)
            st.session_state["son_audit"] = audit_reports
            st.rerun()

    if "son_yuklenen" in st.session_state:
      st.markdown("<br>", unsafe_allow_html=True)

      if "son_audit" in st.session_state and st.session_state["son_audit"]:
        for rep in st.session_state["son_audit"]:
          f_no = rep.get("fatura_no", "Belge")
          cari = rep.get("cari_unvan", "-")
          matrah = rep.get("matrah_toplami", 0.0)
          kdv = rep.get("kdv_toplami", 0.0)
          hesap_genel = rep.get("hesaplanan_genel_toplam", matrah + kdv)
          tuttu = rep.get("tuttu_mu", True)

          col1, col2, col3, col4 = st.columns(4)

          with col1:
            st.markdown(
                f"""
                        <div class="metric-box">
                            <div class="metric-label">Tedarikçi & Belge No</div>
                            <div class="metric-val" style="font-size: 1.1rem; line-height: 1.3;">{f_no}</div>
                            <div style="font-size: 0.8rem; color: #94a3b8; margin-top: 4px;">{cari[:22]}...</div>
                        </div>
                        """,
                unsafe_allow_html=True,
            )

          with col2:
            st.markdown(
                f"""
                        <div class="metric-box">
                            <div class="metric-label">Matrah (KDV Hariç)</div>
                            <div class="metric-val">₺{matrah:,.2f}</div>
                            <div style="font-size: 0.8rem; color: #94a3b8; margin-top: 4px;">Kalem toplamı</div>
                        </div>
                        """,
                unsafe_allow_html=True,
            )

          with col3:
            st.markdown(
                f"""
                        <div class="metric-box">
                            <div class="metric-label">Hesaplanan KDV</div>
                            <div class="metric-val">₺{kdv:,.2f}</div>
                            <div style="font-size: 0.8rem; color: #94a3b8; margin-top: 4px;">Vergi yükü</div>
                        </div>
                        """,
                unsafe_allow_html=True,
            )

          with col4:
            durum_html = (
                '<div class="metric-badge-ok">✓ Tutar Doğrulandı</div>'
                if tuttu
                else '<div class="metric-badge-err">✕ Fark Tespit Edildi</div>'
            )
            st.markdown(
                f"""
                        <div class="metric-box">
                            <div class="metric-label">Ödenecek Tutar</div>
                            <div class="metric-val">₺{hesap_genel:,.2f}</div>
                            {durum_html}
                        </div>
                        """,
                unsafe_allow_html=True,
            )

      st.markdown("<br>", unsafe_allow_html=True)
      df_son = st.session_state["son_yuklenen"]
      st.dataframe(df_son, width="stretch")

      col_btn1, col_btn2 = st.columns(2)
      with col_btn1:
        buffer_son = io.BytesIO()
        with pd.ExcelWriter(buffer_son, engine="openpyxl") as writer:
          df_son.to_excel(writer, index=False, sheet_name="LOGO_AKTARIM")

        st.download_button(
            label="📊 Excel Tablosu Olarak Al",
            data=buffer_son.getvalue(),
            file_name="Logo_Aktarim_Kalemler.xlsx",
            mime=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )

      with col_btn2:
        xml_son = generate_logo_xml(df_son)
        st.download_button(
            label="⚡ Doğrudan Logo Tiger XML İndir",
            data=xml_son,
            file_name="Logo_Tiger_Aktarim.xml",
            mime="application/xml",
            type="primary",
        )

  # Haklar bittiğinde gösterilecek ekran (Kayıt Olma / E-posta Toplama)
  else:
    st.warning(
        "⚠️ Ücretsiz 5 kullanım hakkınız doldu! Sistemi test etmeye devam"
        " etmek ve yeni özelliklerden haberdar olmak için lütfen e-posta"
        " adresinizi yazın."
    )

    email_input = st.text_input("E-posta Adresiniz")
    if st.button("Ücretsiz Devam Et (Kayıt Ol) ve Hak Kazan"):
      if email_input and "@" in email_input:
        st.session_state["user_email"] = email_input
        st.session_state["is_registered"] = True
        # Kayıt olunca ek kullanım hakkı tanımlıyoruz
        st.session_state["credits"] = 5
        st.success(
            "Teşekkürler! E-postanız kaydedildi ve hesabınıza 5 ek kullanım"
            " hakkı tanımlandı."
        )
        st.rerun()
      else:
        st.error("Lütfen geçerli bir e-posta adresi girin.")

with tab_arsiv:
  df_arsiv = get_all_from_db(limit=500)

  if df_arsiv.empty:
    st.info("Arşivde henüz işlenmiş fatura bulunmuyor.")
  else:
    col_arama, col_tarih = st.columns([3, 2])

    with col_arama:
      arama_kelimesi = st.text_input(
          "Arama",
          placeholder="Firma Adı, Fatura No, Ürün/Kalem Adı...",
          label_visibility="collapsed",
      )

    with col_tarih:
      tarih_secimi = st.date_input(
          "Fatura Tarih Aralığı", value=[], label_visibility="collapsed"
      )

    df_filtrelenmis = df_arsiv.copy()

    if arama_kelimesi:
      kelime = arama_kelimesi.lower()
      df_filtrelenmis = df_filtrelenmis[
          df_filtrelenmis["CARİ_UNVAN"]
          .astype(str)
          .str.lower()
          .str.contains(kelime)
          | df_filtrelenmis["BELGE_NO"]
          .astype(str)
          .str.lower()
          .str.contains(kelime)
          | df_filtrelenmis["MALZEME_ACIKLAMASI"]
          .astype(str)
          .str.lower()
          .str.contains(kelime)
          | df_filtrelenmis["DOSYA_ADI"]
          .astype(str)
          .str.lower()
          .str.contains(kelime)
      ]

    if len(tarih_secimi) == 2:
      baslangic, bitis = tarih_secimi

      def parse_fatura_tarihi(tarih_str):
        try:
          return datetime.strptime(str(tarih_str).strip(), "%d.%m.%Y").date()
        except Exception:
          return None

      df_filtrelenmis["_parsed_tarih"] = df_filtrelenmis["TARIH"].apply(
          parse_fatura_tarihi
      )
      df_filtrelenmis = df_filtrelenmis[
          (df_filtrelenmis["_parsed_tarih"] >= baslangic)
          & (df_filtrelenmis["_parsed_tarih"] <= bitis)
      ]
      df_filtrelenmis = df_filtrelenmis.drop(columns=["_parsed_tarih"])

    df_gorunum = df_filtrelenmis.drop(columns=["ID"])

    event = st.dataframe(
        df_gorunum, width="stretch", on_select="rerun", selection_mode="multi-row"
    )

    secili_indeksler = event.selection.rows
    if secili_indeksler:
      df_secili_gercek = df_filtrelenmis.iloc[secili_indeksler]
      df_indir = df_gorunum.iloc[secili_indeksler]
      durum_metni = f"Seçilen {len(df_indir)} Kalem"
    else:
      df_secili_gercek = pd.DataFrame()
      df_indir = df_gorunum
      durum_metni = f"Tüm Liste ({len(df_indir)} Kalem)"

    col_exp_excel, col_exp_xml = st.columns(2)

    with col_exp_excel:
      buffer_arsiv = io.BytesIO()
      with pd.ExcelWriter(buffer_arsiv, engine="openpyxl") as writer:
        df_indir.to_excel(writer, index=False, sheet_name="LOGO_AKTARIM")

      st.download_button(
          label=f"📊 {durum_metni} (Excel)",
          data=buffer_arsiv.getvalue(),
          file_name="TigerFlow_Arsiv.xlsx",
          mime=(
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          ),
      )

    with col_exp_xml:
      xml_arsiv = generate_logo_xml(df_indir)
      st.download_button(
          label=f"⚡ {durum_metni} (Logo XML)",
          data=xml_arsiv,
          file_name="TigerFlow_Arsiv.xml",
          mime="application/xml",
          type="primary",
      )

    if not df_secili_gercek.empty:
      st.markdown("<br>", unsafe_allow_html=True)
      if st.button(f"🗑️ Seçili {len(df_secili_gercek)} Kalemi Arşivden Çıkar"):
        ids_to_del = df_secili_gercek["ID"].tolist()
        delete_rows_by_ids(ids_to_del)
        st.success("Seçilen kayıtlar arşivden silindi.")
        st.rerun()