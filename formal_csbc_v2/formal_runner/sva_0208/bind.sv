module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire key_q,
    input wire rst_ni,
    input wire clk_i
);

assert property (@(posedge clk_i) !rst_ni |-> key_q == '0);

endmodule


bind keymgr_sideload_key sva_checker sva_checker_inst (.*);
