module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire data_valid,
    input wire hw_key_sel,
    input wire data_sw_en,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (data_valid) |-> (hw_key_sel && data_sw_en));

endmodule


bind keymgr sva_checker sva_checker_inst (.*);
