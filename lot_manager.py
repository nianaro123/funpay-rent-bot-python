# lot_manager.py

from bs4 import BeautifulSoup


class LotManager:
    def __init__(self, acc):
        self.acc = acc

    def _get_edit_form_html(self, lot_id: int) -> str:
        response = self.acc.method(
            "get",
            f"lots/offerEdit?offer={lot_id}",
            {
                "accept": "*/*",
                "content-type": "application/json",
                "x-requested-with": "XMLHttpRequest",
            },
            {},
            raise_not_200=True,
        )
        return response.text

    def _parse_form_fields(self, html: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form", {"class": "form-offer-editor"})
        if not form:
            raise RuntimeError("Не найдена форма редактирования лота")

        fields = {}

        for tag in form.find_all("input"):
            name = tag.get("name")
            if not name:
                continue

            input_type = (tag.get("type") or "").lower()

            if input_type == "checkbox":
                if tag.has_attr("checked"):
                    fields[name] = tag.get("value", "on")
            elif input_type == "radio":
                if tag.has_attr("checked"):
                    fields[name] = tag.get("value", "")
            else:
                fields[name] = tag.get("value", "")

        for tag in form.find_all("textarea"):
            name = tag.get("name")
            if not name:
                continue
            fields[name] = tag.text or ""

        for tag in form.find_all("select"):
            name = tag.get("name")
            if not name:
                continue

            selected = tag.find("option", selected=True)
            if selected:
                fields[name] = selected.get("value", "")
            else:
                first = tag.find("option")
                fields[name] = first.get("value", "") if first else ""

        return fields

    def get_lot_fields(self, lot_id: int) -> dict:
        html = self._get_edit_form_html(lot_id)
        return self._parse_form_fields(html)

    def get_summary_fields(self, lot_id: int) -> tuple[str | None, str | None]:
        fields = self.get_lot_fields(lot_id)
        ru = fields.get("fields[summary][ru]")
        en = fields.get("fields[summary][en]")
        return ru, en

    @staticmethod
    def make_busy_title_ru(old_title: str) -> str:
        if old_title.startswith("Занят!"):
            return old_title
        if old_title.startswith("Свободен!"):
            return old_title.replace("Свободен!", "Занят!", 1)
        return f"Занят! {old_title}"

    @staticmethod
    def make_free_title_ru(old_title: str) -> str:
        if old_title.startswith("Свободен!"):
            return old_title
        if old_title.startswith("Занят!"):
            return old_title.replace("Занят!", "Свободен!", 1)
        return f"Свободен! {old_title}"

    @staticmethod
    def make_busy_title_en(old_title: str) -> str:
        if old_title.startswith("Busy!"):
            return old_title
        if old_title.startswith("Free!"):
            return old_title.replace("Free!", "Busy!", 1)
        return f"Busy! {old_title}"

    @staticmethod
    def make_free_title_en(old_title: str) -> str:
        if old_title.startswith("Free!"):
            return old_title
        if old_title.startswith("Busy!"):
            return old_title.replace("Busy!", "Free!", 1)
        return f"Free! {old_title}"

    def update_titles(self, lot_id: int, ru_title: str | None = None, en_title: str | None = None) -> bool:
        fields = self.get_lot_fields(lot_id)

        if ru_title is not None:
            if "fields[summary][ru]" not in fields:
                raise RuntimeError("Не найдено поле fields[summary][ru]")
            fields["fields[summary][ru]"] = ru_title

        if en_title is not None:
            if "fields[summary][en]" not in fields:
                raise RuntimeError("Не найдено поле fields[summary][en]")
            fields["fields[summary][en]"] = en_title

        response = self.acc.method(
            "post",
            "lots/offerSave",
            {
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
            },
            fields,
            raise_not_200=True,
        )

        try:
            data = response.json()
            if data.get("error"):
                raise RuntimeError(f"FunPay вернул ошибку: {data}")
        except Exception:
            pass

        return True

    def set_lot_busy(self, lot_id: int) -> bool:
        ru, en = self.get_summary_fields(lot_id)

        new_ru = self.make_busy_title_ru(ru) if ru else None
        new_en = self.make_busy_title_en(en) if en else None

        return self.update_titles(lot_id, new_ru, new_en)

    def set_lot_free(self, lot_id: int) -> bool:
        ru, en = self.get_summary_fields(lot_id)

        new_ru = self.make_free_title_ru(ru) if ru else None
        new_en = self.make_free_title_en(en) if en else None

        return self.update_titles(lot_id, new_ru, new_en)

    def set_lot_active(self, lot_id: int, is_active: bool) -> bool:
        fields = self.get_lot_fields(lot_id)
        active_keys = (
            "active",
            "fields[active]",
            "offer_active",
            "fields[public]",
        )

        changed = False
        for key in active_keys:
            if key not in fields:
                continue

            if is_active:
                fields[key] = fields[key] or "on"
            else:
                fields.pop(key, None)
            changed = True

        if not changed:
            raise RuntimeError("Не найдено поле активности лота для переключения")

        response = self.acc.method(
            "post",
            "lots/offerSave",
            {
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
            },
            fields,
            raise_not_200=True,
        )

        try:
            data = response.json()
            if data.get("error"):
                raise RuntimeError(f"FunPay вернул ошибку: {data}")
        except Exception:
            pass

        return True
