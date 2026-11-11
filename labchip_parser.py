"""Read the peak table from a text-based LabChip GX report page."""

import re


def reported_peak_count(page_text):
    """Read the instrument's Peak Count, when that summary row is present."""
    for line in page_text.splitlines()[:10]:
        parts = line.split()
        if parts and parts[-1].lower() in {"yes", "no"} and any(
            re.fullmatch(r"[A-P]0?\d{1,2}", part, re.I) for part in parts
        ):
            # Reports with Total Conc. put a decimal number after Peak Count.
            count_index = -3 if len(parts) >= 3 and re.fullmatch(r"\d+\.\d+", parts[-2]) else -2
            if len(parts) >= abs(count_index) and re.fullmatch(r"\d+", parts[count_index]):
                return int(parts[count_index])
        match = re.match(r"Labchip_\S+\s+(\d+)\s+\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s*$", line, re.I)
        if match:
            return int(match.group(1))
    return None


def legacy_peak_row_count(page_text):
    """Count visible rows in older LabChip tables without a Peak Count field.

    This is an independent check against the coordinate-based cell parser. A
    malformed or wrapped row is counted here and must not silently disappear.
    """
    lines = page_text.splitlines()
    for index in range(len(lines) - 1):
        if not re.fullmatch(r"Sample\s+Name\s+Migration\s+Time\s+%\s+Purity\s+Corr\.", lines[index].strip(), re.I):
            continue
        if not re.fullmatch(r"\(sec\)\s*-\s*Center\s+Area", lines[index + 1].strip(), re.I):
            continue
        for end in range(index + 2, len(lines)):
            if re.match(r"LabChip\s+GX\s+Software\b", lines[end], re.I):
                return sum(bool(line.strip()) for line in lines[index + 2:end])
    return None


def _number(words):
    for word in words:
        value = word["text"].strip().replace(",", "")
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            return float(value)
    return None


def _header_columns(words, page_width):
    """Locate peak-table columns by their labels, regardless of their order."""
    def normalized(word):
        return re.sub(r"[^a-z\u4e00-\u9fff]", "", word["text"].lower())

    migration = next(
        (w for w in words if normalized(w) in {
            "migration", "migrationtime", "migrationtimesec",
            "retention", "retentiontime", "retentiontimesec", "迁移时间", "保留时间",
        }),
        None,
    )
    if migration is None:
        migration = next(
            (w for w in words if normalized(w) in {"time", "timesec"} and any(
                normalized(other) in {"purity", "纯度"} and abs(other["top"] - w["top"]) < 14
                for other in words
            )),
            None,
        )
    if migration is None:
        return None
    header = [w for w in words if migration["top"] - 2 <= w["top"] <= migration["top"] + 14]

    def find(*names):
        return next((w for w in header if normalized(w) in names), None)

    purity = find("purity", "puritypercentage", "percentagepurity", "relativepurity", "纯度")
    if purity is None:
        return None
    area = find("area", "corrarea", "correctedarea", "面积", "峰面积", "校正面积")
    labels = {
        "well": find("well", "welllabel", "孔位"),
        "sample": find("sample", "samplename", "样品", "样品名称"),
        "migration": migration,
        "area": area,
        "purity": purity,
        "size": find("size", "分子量"),
        "height": find("height", "峰高"),
    }

    def x1(word):
        return word.get("x1", word["x0"] + max(4, len(word["text"]) * 4))

    starts = {}
    for label, word in labels.items():
        if word is None:
            continue
        start = word["x0"]
        if label == "area":
            prefixes = [w for w in header if normalized(w) in {"corr", "corrected"}
                        and 0 <= start - x1(w) <= 16]
            if prefixes:
                start = min(start, min(w["x0"] for w in prefixes))
        elif label == "purity":
            prefixes = [w for w in header if w["text"] == "%"
                        and 0 <= start - x1(w) <= 8]
            if prefixes:
                start = min(start, min(w["x0"] for w in prefixes))
        starts[label] = start

    ordered = sorted(starts.items(), key=lambda item: item[1])
    bounds = []
    for index, (_, start) in enumerate(ordered[:-1]):
        next_start = ordered[index + 1][1]
        column_words = [w for w in header if start <= w["x0"] < next_start]
        end = max((x1(w) for w in column_words), default=start)
        bounds.append((min(end, next_start) + next_start) / 2)

    columns = {}
    for index, (label, start) in enumerate(ordered):
        left = bounds[index - 1] if index else max(0, start - 8)
        right = bounds[index] if index < len(bounds) else page_width
        columns[label] = (left, right)
    header_bottom = max(w["bottom"] for w in header)
    return columns, header_bottom


def zero_peak_table_is_empty(page):
    """Confirm that a reported zero-peak page has no visible table data rows."""
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
    layout = _header_columns(words, page.width)
    if layout is None:
        return False
    columns, header_bottom = layout
    sample_left, sample_right = columns["sample"] if "sample" in columns else (0, columns["migration"][0])
    footer_top = min(
        (
            word["top"] for word in words
            if word["top"] > header_bottom and (
                word["text"].lower() == "labchip"
                or (word["text"].lower() == "analysis" and any(
                    other["text"].lower().rstrip(":") == "version"
                    and abs(other["top"] - word["top"]) <= 3
                    for other in words
                ))
            )
        ),
        default=page.height,
    )
    return not any(
        header_bottom + 1 < word["top"] < footer_top
        and sample_left <= word["x0"] < sample_right
        and re.search(r"[A-Za-z\u4e00-\u9fff]", word["text"])
        for word in words
    )


def parse_labchip_page(page, page_text):
    """Return the app's sample/peak schema, or None for an unsupported page."""
    sample_match = re.search(r"Sample\s*<([^>]+)>\s*Page\b", page_text, re.I)
    vial_match = re.search(r"#\s*([A-Z]\d{1,2})\s*:", page_text, re.I)
    if not vial_match:
        # Older reports only print the well in the Plate Name / Well Label row.
        heading = "\n".join(page_text.splitlines()[:8])
        vial_match = re.search(r"\b([A-P])0?([1-9]|1[0-2])\b", heading, re.I)
    if not sample_match or not vial_match:
        return None
    vial = (vial_match.group(1) + str(int(vial_match.group(2)))).upper() if vial_match.lastindex == 2 else vial_match.group(1).upper()

    words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
    layout = _header_columns(words, page.width)
    if layout is None:
        return None
    columns, header_bottom = layout
    footer_top = min(
        (
            word["top"] for word in words
            if word["top"] > header_bottom and (
                word["text"].lower() == "labchip" or (
                    word["text"].lower() == "analysis" and any(
                        other["text"].lower().rstrip(":") == "version"
                        and abs(other["top"] - word["top"]) <= 3
                        for other in words
                    )
                )
            )
        ),
        default=page.height,
    )

    rows = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if word["top"] <= header_bottom + 1 or word["top"] >= footer_top:
            continue
        if not rows or abs(word["top"] - rows[-1][0]["top"]) > 3:
            rows.append([word])
        else:
            rows[-1].append(word)

    peaks = []
    first_numeric_left = min(columns["migration"][0], columns["purity"][0])
    if "area" in columns:
        first_numeric_left = min(first_numeric_left, columns["area"][0])
    sample_left, sample_right = columns.get("sample", (0, first_numeric_left))

    def value_in(row, label):
        left, right = columns[label]
        return _number(
            w for w in row
            if left <= (w["x0"] + w.get("x1", w["x0"] + max(4, len(w["text"]) * 4))) / 2 < right
        )

    for row in rows:
        # The LabChip peak table repeats the sample name on every data row.
        if not any(sample_left <= w["x0"] < sample_right and re.search(r"[A-Za-z\u4e00-\u9fff]", w["text"])
                   for w in row):
            continue
        migration_time = value_in(row, "migration")
        peak_purity = value_in(row, "purity")
        area_value = value_in(row, "area") if "area" in columns else 0.0
        if migration_time is None or area_value is None:
            continue
        # Only the first, system-marker peak may have a blank purity cell.
        # Treat a later blank as an incomplete table, even if Peak Count matches.
        if peak_purity is None and peaks:
            return None
        if not (0 < migration_time < 100 and area_value >= 0):
            continue
        if peak_purity is not None and not (0 <= peak_purity <= 100):
            continue
        peaks.append({
            "Migration Time": migration_time,
            "Purity": peak_purity if peak_purity is not None else 0.0,
            "Area": area_value,
        })

    # A marker-only buffer page has an area but no reportable purity.
    count = reported_peak_count(page_text)
    if count is None:
        count = legacy_peak_row_count(page_text)
    if count is None or len(peaks) != count:
        return None
    if not any(peak["Purity"] > 0 for peak in peaks):
        return None
    return {
        "Sample Name": sample_match.group(1).strip(),
        "Vial": vial,
        "Peaks": peaks,
        "Area Missing": "area" not in columns,
    }
