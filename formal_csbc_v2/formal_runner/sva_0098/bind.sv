module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire clr_i,
    input wire init_q,
    input wire out_o,
    input wire NonInitClr,
    input wire clk,
    input wire rst_n
);

assert property (@(posedge clk) disable iff (!rst_n) (clr_i && !init_q && (NonInitClr == 1'b0)) |=> (out_o == 1'b0));

endmodule


bind keymgr_cfg_en sva_checker sva_checker_inst (.*);
