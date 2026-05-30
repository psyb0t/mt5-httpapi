from __future__ import annotations

from mt5api.backtest.optimization_parser import parse_optimization_report


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_optimization_report_sorts_by_result_and_coerces_numbers(tmp_path):
    path = _write(
        tmp_path,
        "optimization.xml",
        """<?xml version="1.0"?>
        <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
                  xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
          <Worksheet>
            <Table>
              <Row>
                <Cell><Data>Pass</Data></Cell>
                <Cell><Data>Result</Data></Cell>
                <Cell><Data>Profit</Data></Cell>
                <Cell><Data>FastPeriod</Data></Cell>
              </Row>
              <Row>
                <Cell><Data>1</Data></Cell>
                <Cell><Data>10.5</Data></Cell>
                <Cell><Data>100.25</Data></Cell>
                <Cell><Data>12</Data></Cell>
              </Row>
              <Row>
                <Cell><Data>2</Data></Cell>
                <Cell><Data>12.75</Data></Cell>
                <Cell><Data>90</Data></Cell>
                <Cell><Data>10</Data></Cell>
              </Row>
            </Table>
          </Worksheet>
        </Workbook>
        """,
    )
    rows = parse_optimization_report(str(path))
    assert len(rows) == 2
    assert rows[0]["Pass"] == 2
    assert rows[0]["Result"] == 12.75
    assert rows[0]["Profit"] == 90
    assert rows[1]["Pass"] == 1
    assert rows[1]["FastPeriod"] == 12


def test_parse_optimization_report_respects_top_n(tmp_path):
    path = _write(
        tmp_path,
        "optimization.xml",
        """<?xml version="1.0"?>
        <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">
          <Worksheet>
            <Table>
              <Row><Cell><Data>Pass</Data></Cell><Cell><Data>Result</Data></Cell></Row>
              <Row><Cell><Data>1</Data></Cell><Cell><Data>1</Data></Cell></Row>
              <Row><Cell><Data>2</Data></Cell><Cell><Data>3</Data></Cell></Row>
              <Row><Cell><Data>3</Data></Cell><Cell><Data>2</Data></Cell></Row>
            </Table>
          </Worksheet>
        </Workbook>
        """,
    )
    rows = parse_optimization_report(str(path), top_n=2)
    assert [row["Pass"] for row in rows] == [2, 3]


def test_parse_optimization_report_handles_missing_result_column(tmp_path):
    path = _write(
        tmp_path,
        "optimization.xml",
        """<?xml version="1.0"?>
        <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">
          <Worksheet>
            <Table>
              <Row><Cell><Data>Pass</Data></Cell><Cell><Data>Profit</Data></Cell></Row>
              <Row><Cell><Data>1</Data></Cell><Cell><Data>5</Data></Cell></Row>
            </Table>
          </Worksheet>
        </Workbook>
        """,
    )
    rows = parse_optimization_report(str(path))
    assert rows == [{"Pass": 1, "Profit": 5}]


def test_parse_optimization_report_respects_sparse_excel_columns(tmp_path):
    path = _write(
        tmp_path,
        "optimization.xml",
        """<?xml version="1.0"?>
        <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
                  xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
          <Worksheet>
            <Table>
              <Row>
                <Cell><Data>Pass</Data></Cell>
                <Cell><Data>Result</Data></Cell>
                <Cell><Data>Profit</Data></Cell>
                <Cell><Data>Profit Factor</Data></Cell>
              </Row>
              <Row>
                <Cell><Data>7</Data></Cell>
                <Cell><Data>19.4</Data></Cell>
                <Cell ss:Index="4"><Data>1.42</Data></Cell>
              </Row>
            </Table>
          </Worksheet>
        </Workbook>
        """,
    )
    rows = parse_optimization_report(str(path))
    assert rows == [{"Pass": 7, "Result": 19.4, "Profit": "", "Profit Factor": 1.42}]


def test_parse_optimization_report_returns_empty_on_malformed_xml(tmp_path):
    path = _write(tmp_path, "broken.xml", "<Workbook")
    assert parse_optimization_report(str(path)) == []


def test_parse_optimization_report_returns_empty_on_empty_sheet(tmp_path):
    path = _write(
        tmp_path,
        "empty.xml",
        """<?xml version="1.0"?>
        <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">
          <Worksheet><Table /></Worksheet>
        </Workbook>
        """,
    )
    assert parse_optimization_report(str(path)) == []