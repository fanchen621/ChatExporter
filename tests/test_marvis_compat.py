import unittest

from chat_exporter.adapters.marvis import MarvisAdapter


class MarvisSchemaCompatibilityTests(unittest.TestCase):
    def test_missing_model_id_is_aliased(self):
        sql = MarvisAdapter._message_select_sql({
            "message_id", "conversation_id", "role", "content", "created_at"
        })
        self.assertIn("NULL AS model_id", sql)
        self.assertIn("NULL AS tool_calls", sql)
        self.assertIn("NULL AS metadata", sql)

    def test_existing_model_id_is_selected_directly(self):
        sql = MarvisAdapter._message_select_sql({
            "message_id", "conversation_id", "role", "content", "created_at", "model_id"
        })
        self.assertIn("model_id", sql)
        self.assertNotIn("NULL AS model_id", sql)

    def test_order_falls_back_without_message_seq(self):
        self.assertEqual(
            MarvisAdapter._message_order_sql({"message_id", "created_at"}),
            "created_at ASC",
        )
        self.assertEqual(
            MarvisAdapter._message_order_sql({"message_id"}),
            "message_id ASC",
        )


if __name__ == "__main__":
    unittest.main()
