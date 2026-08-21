import csv
import io
from fastapi.responses import StreamingResponse
from typing import List, Any

class CsvReportBuilder:
    """Utility to generate CSV streaming responses.

    Example usage:
        builder = CsvReportBuilder(filename='report.csv', headers=['Column1', 'Column2'])
        builder.add_row([value1, value2])
        response = builder.build()
    """

    def __init__(self, filename: str, headers: List[str]):
        self.filename = filename
        self.headers = headers
        self._rows: List[List[Any]] = []

    def add_row(self, row: List[Any]):
        self._rows.append(row)

    def build(self) -> StreamingResponse:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(self.headers)
        for row in self._rows:
            writer.writerow(row)
        buffer.seek(0)
        return StreamingResponse(
            buffer,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={self.filename}"},
        )
